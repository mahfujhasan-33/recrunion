from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import httpx
from pypdf import PdfWriter
from sqlalchemy import Text, select
from sqlalchemy.orm import Session

from app.adapters.blogger import BloggerPublisher
from app.adapters.document_storage import LocalDocumentStorage
from app.adapters.drive import DriveArchivedDocument, DriveFolder
from app.adapters.gmail import GmailApplicationMessage, GmailApplicationSource, GmailAttachment
from app.adapters.publisher import JobPublicationContent, PublicationResult
from app.dependencies import (
    build_assistant_service,
    build_email_intake_service,
    build_job_publishing_service,
)
from app.errors import DriveArchiveError, PublisherUnavailableError
from app.models.applications import (
    ApplicationIntakeReceipt,
    ApplicationSource,
    DriveArchiveStatus,
)
from app.models.assistant import AssistantConversation
from app.models.jobs import JobStatus
from app.models.processing_jobs import ProcessingJob, ProcessingJobStatus, ProcessingJobType
from app.repositories.applications import ApplicationRepository
from app.repositories.assistant import AssistantRepository
from app.repositories.email_intake import EmailIntakeRepository
from app.repositories.jobs import JobRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.schemas.job_publications import PublicationProvider
from app.schemas.jobs import JobWriteRequest
from app.services.jobs import JobService


class FakeOAuth:
    configured = True

    async def access_token(self, _client: httpx.AsyncClient) -> str:
        return "safe-test-token"


class FakeNamedPublisher:
    def __init__(self, provider: str, *, fail: bool = False) -> None:
        self.provider = provider
        self.fail = fail
        self.contents: list[JobPublicationContent] = []

    async def publish_job(self, content: JobPublicationContent) -> PublicationResult:
        self.contents.append(content)
        if self.fail:
            raise PublisherUnavailableError(f"{self.provider} is unavailable.")
        return PublicationResult(
            provider=self.provider,
            external_post_uri=f"{self.provider.casefold()}://post/1",
            external_record_id="1",
            external_url=f"https://example.test/{self.provider.casefold()}/1",
            published_at=content.created_at,
        )


class FakeGmailSource:
    def __init__(self, messages: list[GmailApplicationMessage]) -> None:
        self.messages = messages
        self.codes: list[str] = []

    async def fetch_job_applications(self, application_code: str) -> list[GmailApplicationMessage]:
        self.codes.append(application_code)
        return self.messages


class FakeDriveArchive:
    def __init__(self) -> None:
        self.folders: list[str] = []
        self.files: list[tuple[str, str, bytes]] = []
        self.fail = False

    async def create_folder(self, name: str) -> DriveFolder:
        self.folders.append(name)
        return DriveFolder("folder-1", "https://drive.test/folder-1")

    async def archive_pdf(
        self, folder_id: str, filename: str, content: bytes
    ) -> DriveArchivedDocument:
        if self.fail:
            raise DriveArchiveError("Drive archive is temporarily unavailable.")
        self.files.append((folder_id, filename, content))
        return DriveArchivedDocument("file-1", "https://drive.test/file-1")


def _approved_job(db_session: Session, job_payload: dict[str, Any]):
    repository = JobRepository(db_session)
    response = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    job = repository.get(response.id)
    assert job is not None
    job.jd_content = "## Summary\nBuild dependable systems."
    job.jd_generated_content = job.jd_content
    job.jd_version = 2
    job.status = JobStatus.APPROVED
    job.approved_at = datetime.now(UTC)
    repository.update(job)
    return response.id


def _pdf() -> bytes:
    target = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(target)
    return target.getvalue()


async def test_multichannel_publication_keeps_independent_provider_results(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job_id = _approved_job(db_session, job_payload)
    blogger = FakeNamedPublisher("BLOGGER")
    bluesky = FakeNamedPublisher("BLUESKY")
    service = build_job_publishing_service(db_session, bluesky, blogger)

    batch = service.publish(
        job_id,
        [PublicationProvider.BLOGGER, PublicationProvider.BLUESKY],
    )
    await service.process(batch.processing_task_id, lambda _value, _message: None)

    results = {item.provider: item for item in service.list_latest(job_id)}
    assert set(results) == {"BLOGGER", "BLUESKY"}
    assert all(item.status == "PUBLISHED" for item in results.values())
    assert "example.test/blogger" in bluesky.contents[0].short_text
    assert blogger.contents[0].jd_version == 2


async def test_multichannel_partial_failure_does_not_erase_success(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job_id = _approved_job(db_session, job_payload)
    blogger = FakeNamedPublisher("BLOGGER")
    bluesky = FakeNamedPublisher("BLUESKY", fail=True)
    service = build_job_publishing_service(db_session, bluesky, blogger)

    batch = service.publish(
        job_id,
        [PublicationProvider.BLOGGER, PublicationProvider.BLUESKY],
    )
    await service.process(batch.processing_task_id, lambda _value, _message: None)

    results = {item.provider: item.status for item in service.list_latest(job_id)}
    assert results == {"BLOGGER": "PUBLISHED", "BLUESKY": "FAILED"}
    assert JobRepository(db_session).get(job_id).status == JobStatus.PUBLISHED


async def test_blogger_publisher_maps_official_rest_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/blogger/v3/blogs/blog-1/posts"
        assert request.headers["authorization"] == "Bearer safe-test-token"
        return httpx.Response(
            200,
            json={
                "id": "post-1",
                "url": "https://blog.test/post-1",
                "published": "2026-10-12T01:02:03Z",
            },
        )

    adapter = BloggerPublisher(
        blog_id="blog-1",
        oauth=FakeOAuth(),  # type: ignore[arg-type]
        api_base_url="https://google.test/blogger/v3",
        transport=httpx.MockTransport(handler),
    )
    result = await adapter.publish_job(
        JobPublicationContent(
            title="Engineer",
            short_text="We're hiring",
            full_html="<p>Approved JD</p>",
            jd_version=3,
            created_at=datetime.now(UTC),
        )
    )

    assert result.provider == "BLOGGER"
    assert result.external_record_id == "post-1"
    assert result.external_url == "https://blog.test/post-1"


async def test_gmail_source_uses_exact_subject_and_decodes_pdf_attachment() -> None:
    seen_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        if request.url.path.endswith("/messages"):
            seen_query = request.url.params["q"]
            return httpx.Response(200, json={"messages": [{"id": "message-1"}]})
        return httpx.Response(
            200,
            json={
                "id": "message-1",
                "internalDate": "1791772800000",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "[RU-1234ABCD] Application"},
                        {
                            "name": "From",
                            "value": "Synthetic Candidate <Candidate.Sender@Example.COM>",
                        },
                    ],
                    "parts": [
                        {
                            "filename": "candidate.pdf",
                            "mimeType": "application/pdf",
                            "body": {"size": 4, "data": "JVBERg"},
                        }
                    ],
                },
            },
        )

    source = GmailApplicationSource(
        oauth=FakeOAuth(),  # type: ignore[arg-type]
        api_base_url="https://gmail.test/gmail/v1",
        transport=httpx.MockTransport(handler),
    )
    messages = await source.fetch_job_applications("RU-1234ABCD")

    assert seen_query == 'subject:"[RU-1234ABCD] Application" has:attachment'
    assert messages[0].attachments[0].content == b"%PDF"
    assert messages[0].sender_email == "Candidate.Sender@example.com"


async def test_email_intake_reuses_manual_pipeline_and_archives_to_drive(
    db_session: Session,
    tmp_path,
    job_payload: dict[str, Any],
) -> None:
    job_id = _approved_job(db_session, job_payload)
    job = JobRepository(db_session).get(job_id)
    assert job is not None
    attachment = GmailAttachment(
        attachment_id="attachment-1",
        filename="synthetic-candidate.pdf",
        mime_type="application/pdf",
        size=len(_pdf()),
        content=_pdf(),
    )
    source = FakeGmailSource(
        [
            GmailApplicationMessage(
                "message-1",
                datetime.now(UTC),
                (attachment,),
                sender_email="candidate.sender@example.com",
            )
        ]
    )
    drive = FakeDriveArchive()
    service = build_email_intake_service(
        db_session,
        source=source,  # type: ignore[arg-type]
        drive_adapter=drive,  # type: ignore[arg-type]
        storage=LocalDocumentStorage(tmp_path / "applications", 10 * 1024 * 1024),
    )

    queued = service.queue(job_id)
    summary = await service.process(queued.task_id, lambda _value, _message: None)

    applications = ApplicationRepository(db_session).list_for_job(job_id)
    status = service.status(job_id)
    assert summary.imported == 1
    assert applications[0].source == ApplicationSource.EMAIL
    assert applications[0].contact_email == "candidate.sender@example.com"
    assert status.receipts[0].archive_status == DriveArchiveStatus.ARCHIVED
    assert source.codes == [job.application_code]
    assert drive.files[0][1].startswith("APP-")
    assert drive.files[0][1].endswith(".pdf")


async def test_email_intake_persists_long_gmail_attachment_identifier(
    db_session: Session,
    tmp_path,
    job_payload: dict[str, Any],
) -> None:
    job_id = _approved_job(db_session, job_payload)
    content = _pdf()
    attachment_id = "gmail-attachment-" + ("x" * 400)
    service = build_email_intake_service(
        db_session,
        source=FakeGmailSource(
            [
                GmailApplicationMessage(
                    "message-long-attachment-id",
                    datetime.now(UTC),
                    (
                        GmailAttachment(
                            attachment_id,
                            "candidate.pdf",
                            "application/pdf",
                            len(content),
                            content,
                        ),
                    ),
                )
            ]
        ),  # type: ignore[arg-type]
        drive_adapter=FakeDriveArchive(),  # type: ignore[arg-type]
        storage=LocalDocumentStorage(tmp_path / "applications", 10 * 1024 * 1024),
    )

    queued = service.queue(job_id)
    summary = await service.process(queued.task_id, lambda _value, _message: None)

    receipt = service.status(job_id).receipts[0]
    column_type = ApplicationIntakeReceipt.__table__.c.external_attachment_id.type
    assert summary.imported == 1
    assert receipt.status == "IMPORTED"
    assert isinstance(column_type, Text)


async def test_email_intake_retries_receipt_left_received_by_interrupted_sync(
    db_session: Session,
    tmp_path,
    job_payload: dict[str, Any],
) -> None:
    job_id = _approved_job(db_session, job_payload)
    content = _pdf()
    message = GmailApplicationMessage(
        "message-interrupted",
        datetime.now(UTC),
        (
            GmailAttachment(
                "attachment-interrupted",
                "candidate.pdf",
                "application/pdf",
                len(content),
                content,
            ),
        ),
    )
    service = build_email_intake_service(
        db_session,
        source=FakeGmailSource([message]),  # type: ignore[arg-type]
        drive_adapter=FakeDriveArchive(),  # type: ignore[arg-type]
        storage=LocalDocumentStorage(tmp_path / "applications", 10 * 1024 * 1024),
    )
    interrupted = service.queue(job_id)
    repository = EmailIntakeRepository(db_session)
    repository.create_receipt(
        ApplicationIntakeReceipt(
            job_id=job_id,
            processing_task_id=interrupted.task_id,
            source_type=ApplicationSource.EMAIL,
            external_message_id=message.message_id,
            received_at=message.received_at,
        )
    )
    interrupted_task = ProcessingJobRepository(db_session).get(interrupted.task_id)
    assert interrupted_task is not None
    interrupted_task.status = ProcessingJobStatus.FAILED
    db_session.commit()

    retried = service.queue(job_id)
    summary = await service.process(retried.task_id, lambda _value, _message: None)

    receipt = repository.find_message(message.message_id)
    assert summary.imported == 1
    assert receipt is not None
    assert receipt.status == "IMPORTED"
    assert receipt.processing_task_id == retried.task_id


async def test_email_intake_isolates_multiple_pdf_message(
    db_session: Session,
    tmp_path,
    job_payload: dict[str, Any],
) -> None:
    job_id = _approved_job(db_session, job_payload)
    content = _pdf()
    attachments = tuple(
        GmailAttachment(
            f"attachment-{index}", f"cv-{index}.pdf", "application/pdf", len(content), content
        )
        for index in range(2)
    )
    service = build_email_intake_service(
        db_session,
        source=FakeGmailSource(
            [GmailApplicationMessage("message-multiple", datetime.now(UTC), attachments)]
        ),  # type: ignore[arg-type]
        drive_adapter=FakeDriveArchive(),  # type: ignore[arg-type]
        storage=LocalDocumentStorage(tmp_path / "applications", 10 * 1024 * 1024),
    )

    queued = service.queue(job_id)
    summary = await service.process(queued.task_id, lambda _value, _message: None)

    assert summary.needs_review == 1
    assert ApplicationRepository(db_session).list_for_job(job_id) == []


async def test_drive_failure_preserves_local_intake_and_next_sync_retries_archive(
    db_session: Session,
    tmp_path,
    job_payload: dict[str, Any],
) -> None:
    job_id = _approved_job(db_session, job_payload)
    content = _pdf()
    source = FakeGmailSource(
        [
            GmailApplicationMessage(
                "message-retry",
                datetime.now(UTC),
                (
                    GmailAttachment(
                        "attachment-retry",
                        "candidate.pdf",
                        "application/pdf",
                        len(content),
                        content,
                    ),
                ),
            )
        ]
    )
    drive = FakeDriveArchive()
    drive.fail = True
    service = build_email_intake_service(
        db_session,
        source=source,  # type: ignore[arg-type]
        drive_adapter=drive,  # type: ignore[arg-type]
        storage=LocalDocumentStorage(tmp_path / "applications", 10 * 1024 * 1024),
    )

    first = service.queue(job_id)
    await service.process(first.task_id, lambda _value, _message: None)
    assert len(ApplicationRepository(db_session).list_for_job(job_id)) == 1
    assert service.status(job_id).receipts[0].archive_status == "RETRY_PENDING"

    first_task = ProcessingJobRepository(db_session).get(first.task_id)
    assert first_task is not None
    ProcessingJobRepository(db_session).complete(first_task)
    drive.fail = False
    second = service.queue(job_id)
    await service.process(second.task_id, lambda _value, _message: None)

    assert len(ApplicationRepository(db_session).list_for_job(job_id)) == 1
    assert drive.files


def test_email_sync_api_queues_and_reports_status(
    client,
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job_id = _approved_job(db_session, job_payload)

    queued = client.post(f"/api/v1/jobs/{job_id}/applications/email-sync")
    status = client.get(f"/api/v1/jobs/{job_id}/applications/email-sync")

    assert queued.status_code == 202
    assert queued.json()["status"] == "QUEUED"
    assert status.status_code == 200
    assert status.json()["task_id"] == queued.json()["task_id"]


async def test_assistant_fetch_intent_queues_email_service_without_provider_access(
    db_session: Session,
    fake_llm_adapter,
    fake_embedding_adapter,
    fake_publisher_adapter,
    job_payload: dict[str, Any],
) -> None:
    job_id = _approved_job(db_session, job_payload)
    assistant_repository = AssistantRepository(db_session)
    conversation = assistant_repository.create_conversation(
        AssistantConversation(active_job_id=job_id)
    )
    service = build_assistant_service(
        db_session,
        fake_llm_adapter,
        fake_embedding_adapter,
        fake_publisher_adapter,
    )
    queued_turn = service.queue_turn(conversation.id, "Fetch email applications.")
    task_repository = ProcessingJobRepository(db_session)
    turn_task = task_repository.get(queued_turn.task_id)
    assert turn_task is not None

    await service.process_turn(turn_task.entity_id, lambda _value, _message: None)

    tasks = list(
        db_session.scalars(
            select(ProcessingJob).where(
                ProcessingJob.job_type == ProcessingJobType.SYNC_EMAIL_APPLICATIONS
            )
        )
    )
    assert len(tasks) == 1
    assert tasks[0].entity_id == job_id
