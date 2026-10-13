import base64
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.email import EmailSendResult, OutboundEmail
from app.adapters.gmail_email import GmailEmailAdapter
from app.adapters.google_oauth import GoogleOAuthClient, GoogleOAuthCredentials
from app.config import get_settings
from app.dependencies import get_outbound_email_adapter
from app.errors import (
    GmailAuthenticationError,
    GmailConfigurationError,
    GmailSendError,
    InterviewAlreadyScheduledError,
    InterviewNotFoundError,
    InterviewSchedulingError,
)
from app.main import CandidateJoinTokenRedactionFilter
from app.models.applications import (
    ApplicationSource,
    ApplicationStatus,
    Candidate,
    CandidateCVChunk,
    CandidateDocument,
    CandidateDocumentProcessingStatus,
    CandidateProfile,
    JobApplication,
)
from app.models.interviews import InterviewSession, InvitationStatus
from app.models.jobs import (
    EmploymentType,
    Job,
    JobRequirement,
    JobStatus,
    RequirementCategory,
    RequirementType,
)
from app.models.scoring import CandidateScore, EvidenceCoverage
from app.models.screening import CandidateScreening, CandidateScreeningStatus
from app.repositories.interviews import InterviewRepository
from app.repositories.jobs import JobRepository
from app.schemas.candidate_profiles import (
    CandidateContactDetail,
    CandidateProfileData,
    CandidateSummary,
    ContactDetailKind,
)
from app.schemas.interviews import InterviewScheduleRequest, InvitationEmailSource
from app.services.interview_scheduling import InterviewSchedulingService

TEST_SECRET = "test-only-interview-secret-with-at-least-32-characters"


class FakeEmailAdapter:
    def __init__(self) -> None:
        self.messages: list[OutboundEmail] = []
        self.error: GmailConfigurationError | GmailSendError | None = None

    async def send(self, message: OutboundEmail) -> EmailSendResult:
        self.messages.append(message)
        if self.error is not None:
            raise self.error
        return EmailSendResult("gmail-message-1", datetime.now(UTC))


class StaticOAuth:
    configured = True

    async def access_token(self, _client: httpx.AsyncClient) -> str:
        return "safe-access-token"


def create_scored_application(
    session: Session,
    *,
    job: Job | None = None,
    email: str | None = "candidate@example.com",
    contact_email: str | None = None,
) -> tuple[Job, JobApplication]:
    job = job or Job(
        id=uuid4(),
        code=f"JOB-{uuid4().hex[:6]}",
        application_code=f"RU-{uuid4().hex[:8]}",
        title="AI Engineer",
        location="Dhaka",
        employment_type=EmploymentType.FULL_TIME,
        application_email="jobs@example.com",
        status=JobStatus.PUBLISHED,
    )
    if not job.requirements:
        job.requirements = [
            JobRequirement(
                job_id=job.id,
                category=RequirementCategory.SKILL,
                text="Python",
                requirement_type=RequirementType.REQUIRED,
                priority=1,
            )
        ]
    candidate = Candidate(id=uuid4(), display_reference=f"Candidate {uuid4().hex[:6]}")
    application = JobApplication(
        id=uuid4(),
        job_id=job.id,
        candidate_id=candidate.id,
        source=ApplicationSource.MANUAL_UPLOAD,
        status=ApplicationStatus.IMPORTED,
        contact_email=contact_email,
        candidate=candidate,
    )
    document = CandidateDocument(
        id=uuid4(),
        application_id=application.id,
        job_id=job.id,
        original_filename="synthetic.pdf",
        storage_key=f"{job.code}/{uuid4()}.pdf",
        mime_type="application/pdf",
        file_size=128,
        sha256=uuid4().hex + uuid4().hex,
        processing_status=CandidateDocumentProcessingStatus.READY,
        embedding_model="nomic-embed-text",
        embedding_dimension=768,
        chunk_count=1,
    )
    chunk = CandidateCVChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        page_number=1,
        content="Synthetic candidate email and experience evidence.",
        embedding=[1.0] + [0.0] * 767,
    )
    document.chunks = [chunk]
    document.profile = CandidateProfile(
        id=uuid4(),
        candidate_id=candidate.id,
        application_id=application.id,
        source_document_id=document.id,
        structured_json=CandidateProfileData(
            summary=(
                None
                if email is not None
                else CandidateSummary(
                    text="Synthetic candidate profile.",
                    evidence_chunk_ids=[chunk.id],
                )
            ),
            contact_details=(
                [
                    CandidateContactDetail(
                        kind=ContactDetailKind.EMAIL,
                        value=email,
                        evidence_chunk_ids=[chunk.id],
                    )
                ]
                if email is not None
                else []
            ),
        ).model_dump(mode="json"),
        provider="fake",
        model="fake-profile",
    )
    application.document = document
    screening = CandidateScreening(
        id=uuid4(),
        job_id=job.id,
        application_id=application.id,
        candidate_document_id=document.id,
        status=CandidateScreeningStatus.COMPLETED,
        requirements_fingerprint="a" * 64,
        screened_at=datetime.now(UTC),
    )
    score = CandidateScore(
        id=uuid4(),
        job_id=job.id,
        application_id=application.id,
        screening_id=screening.id,
        overall_score=Decimal("80.00"),
        dimension_breakdown={},
        evidence_coverage=EvidenceCoverage.HIGH,
        justification="Deterministic test score.",
        scoring_config_version=1,
        scoring_config_fingerprint="b" * 64,
        source_requirement_fingerprint="a" * 64,
        source_screened_at=screening.screened_at,
        algorithm_version="F2_V1",
    )
    session.add_all([job, application, screening, score])
    session.commit()
    return job, application


def build_service(session: Session, email: FakeEmailAdapter) -> InterviewSchedulingService:
    return InterviewSchedulingService(
        InterviewRepository(session),
        JobRepository(session),
        email,
        app_base_url="http://testserver",
        token_secret=TEST_SECRET,
    )


def future_payload(
    *,
    timezone: str = "Asia/Dhaka",
    duration: int = 45,
    invitation_email: str = "candidate@example.com",
) -> InterviewScheduleRequest:
    local = (datetime.now(UTC) + timedelta(days=2)).astimezone(ZoneInfo(timezone))
    return InterviewScheduleRequest(
        invitation_email=invitation_email,
        scheduled_start_local=local.replace(tzinfo=None, second=0, microsecond=0),
        timezone=timezone,
        duration_minutes=duration,
    )


@pytest.mark.asyncio
async def test_explicit_schedule_persists_relationships_utc_token_and_invitation(
    db_session: Session,
) -> None:
    job, application = create_scored_application(db_session)
    email = FakeEmailAdapter()

    result = await build_service(db_session, email).schedule(
        application.id,
        InterviewScheduleRequest(
            invitation_email="candidate@example.com",
            scheduled_start_local=datetime(2030, 10, 14, 15, 0),
            timezone="Asia/Dhaka",
            duration_minutes=45,
        ),
    )

    persisted = db_session.get(InterviewSession, result.id)
    assert persisted is not None
    assert persisted.job_id == job.id
    assert persisted.application_id == application.id
    assert persisted.candidate_id == application.candidate_id
    assert persisted.invitation_email == "candidate@example.com"
    assert result.scheduled_start_at == datetime(2030, 10, 14, 9, 0, tzinfo=UTC)
    assert result.invitation_status == InvitationStatus.SENT
    token = result.candidate_join_url.rsplit("/", 1)[-1]
    assert token not in persisted.candidate_access_token_hash
    assert len(token) >= 40
    assert len(email.messages) == 1
    invitation = email.messages[0]
    assert invitation.recipient == "candidate@example.com"
    assert "AI Engineer" in invitation.text_body
    assert "Asia/Dhaka" in invitation.text_body
    assert "45 minutes" in invitation.text_body
    assert result.candidate_join_url in invitation.text_body
    assert "score" not in invitation.text_body.casefold()
    assert "rank" not in invitation.text_body.casefold()


@pytest.mark.asyncio
async def test_score_does_not_create_interview_until_explicit_schedule(
    db_session: Session,
) -> None:
    _, application = create_scored_application(db_session)
    assert db_session.scalar(select(func.count()).select_from(InterviewSession)) == 0

    await build_service(db_session, FakeEmailAdapter()).schedule(
        application.id,
        future_payload(),
    )

    assert db_session.scalar(select(func.count()).select_from(InterviewSession)) == 1


@pytest.mark.asyncio
async def test_unscored_application_is_rejected(db_session: Session) -> None:
    _, application = create_scored_application(db_session)
    db_session.execute(
        CandidateScore.__table__.delete().where(CandidateScore.application_id == application.id)
    )
    db_session.commit()

    with pytest.raises(InterviewSchedulingError, match="persisted suitability score"):
        await build_service(db_session, FakeEmailAdapter()).schedule(
            application.id,
            future_payload(),
        )


@pytest.mark.asyncio
async def test_past_invalid_timezone_and_ambiguous_time_are_rejected(
    db_session: Session,
) -> None:
    _, application = create_scored_application(db_session)
    service = build_service(db_session, FakeEmailAdapter())
    with pytest.raises(InterviewSchedulingError, match="future"):
        await service.schedule(
            application.id,
            InterviewScheduleRequest(
                invitation_email="candidate@example.com",
                scheduled_start_local=datetime(2020, 1, 1, 12, 0),
                timezone="UTC",
                duration_minutes=30,
            ),
        )
    with pytest.raises(InterviewSchedulingError, match="not recognized"):
        await service.schedule(
            application.id,
            InterviewScheduleRequest(
                invitation_email="candidate@example.com",
                scheduled_start_local=datetime(2030, 1, 1, 12, 0),
                timezone="Not/A_Zone",
                duration_minutes=30,
            ),
        )
    with pytest.raises(InterviewSchedulingError, match="ambiguous"):
        await service.schedule(
            application.id,
            InterviewScheduleRequest(
                invitation_email="candidate@example.com",
                scheduled_start_local=datetime(2030, 11, 3, 1, 30),
                timezone="America/New_York",
                duration_minutes=30,
            ),
        )


@pytest.mark.asyncio
async def test_duplicate_schedule_is_blocked_without_second_invitation(
    db_session: Session,
) -> None:
    _, application = create_scored_application(db_session)
    email = FakeEmailAdapter()
    service = build_service(db_session, email)
    await service.schedule(application.id, future_payload())

    with pytest.raises(InterviewAlreadyScheduledError):
        await service.schedule(application.id, future_payload())

    assert db_session.scalar(select(func.count()).select_from(InterviewSession)) == 1
    assert len(email.messages) == 1


def test_duration_bounds_are_validated() -> None:
    with pytest.raises(ValidationError):
        future_payload(duration=0)
    with pytest.raises(ValidationError):
        future_payload(duration=241)


@pytest.mark.asyncio
async def test_invitation_failure_is_persisted_and_retry_reuses_interview(
    db_session: Session,
) -> None:
    _, application = create_scored_application(db_session)
    email = FakeEmailAdapter()
    email.error = GmailSendError("Gmail is temporarily unavailable.")
    service = build_service(db_session, email)

    failed = await service.schedule(application.id, future_payload())
    assert failed.invitation_status == InvitationStatus.FAILED
    assert failed.invitation_error_code == "GMAIL_SEND_FAILED"
    application.contact_email = "different-application@example.com"
    assert application.document.profile is not None
    original_profile = application.document.profile.structured_json
    application.document.profile.structured_json = CandidateProfileData(
        contact_details=[
            CandidateContactDetail(
                kind=ContactDetailKind.EMAIL,
                value="different-profile@example.com",
                evidence_chunk_ids=[application.document.chunks[0].id],
            )
        ]
    ).model_dump(mode="json")
    db_session.commit()
    email.error = None

    retried = await service.resend_invitation(failed.id)

    assert retried.id == failed.id
    assert retried.candidate_join_url == failed.candidate_join_url
    assert retried.invitation_status == InvitationStatus.SENT
    assert db_session.scalar(select(func.count()).select_from(InterviewSession)) == 1
    assert len(email.messages) == 2
    assert [message.recipient for message in email.messages] == [
        "candidate@example.com",
        "candidate@example.com",
    ]
    assert original_profile != application.document.profile.structured_json


def test_scheduling_email_precedence_and_blank_fallback(db_session: Session) -> None:
    service = build_service(db_session, FakeEmailAdapter())
    _, email_application = create_scored_application(
        db_session,
        email="cv@example.com",
        contact_email="sender@example.com",
    )
    _, manual_application = create_scored_application(
        db_session,
        email="cv-fallback@example.com",
    )
    _, no_email_application = create_scored_application(db_session, email=None)

    email_defaults = service.get_scheduling_defaults(email_application.id)
    manual_defaults = service.get_scheduling_defaults(manual_application.id)
    blank_defaults = service.get_scheduling_defaults(no_email_application.id)

    assert email_defaults.invitation_email == "sender@example.com"
    assert email_defaults.email_source == InvitationEmailSource.APPLICATION_EMAIL
    assert manual_defaults.invitation_email == "cv-fallback@example.com"
    assert manual_defaults.email_source == InvitationEmailSource.CV_EXTRACTED_EMAIL
    assert blank_defaults.invitation_email is None
    assert blank_defaults.email_source == InvitationEmailSource.NOT_AVAILABLE


@pytest.mark.asyncio
async def test_recruiter_override_is_persisted_without_changing_profile(
    db_session: Session,
) -> None:
    _, application = create_scored_application(
        db_session,
        email="cv@example.com",
        contact_email="sender@example.com",
    )
    assert application.document.profile is not None
    original_profile = application.document.profile.structured_json.copy()
    email = FakeEmailAdapter()

    result = await build_service(db_session, email).schedule(
        application.id,
        future_payload(invitation_email="confirmed@example.com"),
    )

    persisted = db_session.get(InterviewSession, result.id)
    assert persisted is not None
    assert persisted.invitation_email == "confirmed@example.com"
    assert result.invitation_email == "confirmed@example.com"
    assert email.messages[0].recipient == "confirmed@example.com"
    assert application.contact_email == "sender@example.com"
    assert application.document.profile.structured_json == original_profile


def test_invalid_invitation_email_is_rejected() -> None:
    with pytest.raises(ValidationError):
        future_payload(invitation_email="not-an-email")


@pytest.mark.asyncio
async def test_missing_email_configuration_preserves_interview(db_session: Session) -> None:
    _, application = create_scored_application(db_session)
    email = FakeEmailAdapter()
    email.error = GmailConfigurationError("Outbound Gmail invitations are not configured.")

    result = await build_service(db_session, email).schedule(application.id, future_payload())

    assert result.invitation_status == InvitationStatus.NOT_CONFIGURED
    assert db_session.get(InterviewSession, result.id) is not None


@pytest.mark.asyncio
async def test_candidate_tokens_are_scoped_and_wrong_token_fails(db_session: Session) -> None:
    job, first_application = create_scored_application(db_session)
    _, second_application = create_scored_application(db_session, job=job)
    service = build_service(db_session, FakeEmailAdapter())
    first = await service.schedule(first_application.id, future_payload())
    second = await service.schedule(second_application.id, future_payload())
    first_token = first.candidate_join_url.rsplit("/", 1)[-1]
    second_token = second.candidate_join_url.rsplit("/", 1)[-1]

    assert first_token != second_token
    assert service.get_candidate_view(first_token).job_title == job.title
    with pytest.raises(InterviewNotFoundError):
        service.get_candidate_view("wrong-token")


@pytest.mark.asyncio
async def test_persisted_interview_reloads_and_relationship_corruption_is_rejected(
    db_session: Session,
) -> None:
    job, application = create_scored_application(db_session)
    service = build_service(db_session, FakeEmailAdapter())
    scheduled = await service.schedule(application.id, future_payload())
    db_session.expire_all()

    assert [item.id for item in service.list_for_job(job.id)] == [scheduled.id]
    persisted = db_session.get(InterviewSession, scheduled.id)
    assert persisted is not None
    persisted.candidate_id = uuid4()
    db_session.commit()
    with pytest.raises(InterviewSchedulingError, match="relationships"):
        service.get_interview(scheduled.id)


def test_candidate_join_token_is_redacted_from_access_log() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/interviews/join/sensitive-token", "1.1", 200),
        None,
    )

    assert CandidateJoinTokenRedactionFilter().filter(record) is True
    assert "sensitive-token" not in str(record.args)
    assert "/interviews/join/[REDACTED]" in str(record.args)


def test_candidate_landing_page_is_safe(
    application: FastAPI,
    database_engine,
) -> None:
    email = FakeEmailAdapter()
    application.dependency_overrides[get_outbound_email_adapter] = lambda: email
    settings = get_settings()
    original_secret, original_base_url = settings.jwt_secret, settings.app_base_url
    settings.jwt_secret = TEST_SECRET
    settings.app_base_url = "http://testserver"
    try:
        with Session(database_engine, expire_on_commit=False) as session:
            _, candidate_application = create_scored_application(session)
        with TestClient(application) as client:
            scheduled = client.post(
                f"/api/v1/applications/{candidate_application.id}/interviews",
                json=future_payload().model_dump(mode="json"),
            )
            assert scheduled.status_code == 201
            join_url = scheduled.json()["candidate_join_url"]
            page = client.get(join_url)
            assert page.status_code == 200
            assert "AI Engineer" in page.text
            assert "45 minutes" in page.text
            for recruiter_only in (
                "Suitability Score",
                "Match Rank",
                "candidate evidence",
                "recruiter note",
            ):
                assert recruiter_only not in page.text
            assert client.get("/interviews/join/wrong-token").status_code == 404
    finally:
        settings.jwt_secret = original_secret
        settings.app_base_url = original_base_url


def test_scheduling_defaults_api_and_editable_form(
    application: FastAPI,
    database_engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        job, candidate_application = create_scored_application(
            session,
            email="cv@example.com",
            contact_email="sender@example.com",
        )

    with TestClient(application) as client:
        defaults = client.get(
            f"/api/v1/applications/{candidate_application.id}/interview-scheduling-defaults"
        )
        page = client.get(f"/jobs/{job.id}/applications")
        invalid = client.post(
            f"/api/v1/applications/{candidate_application.id}/interviews",
            json={
                **future_payload().model_dump(mode="json"),
                "invitation_email": "not-an-email",
            },
        )

    assert defaults.status_code == 200
    assert defaults.json()["invitation_email"] == "sender@example.com"
    assert defaults.json()["email_source"] == "APPLICATION_EMAIL"
    assert page.status_code == 200
    assert 'name="invitation_email"' in page.text
    assert "Review and edit this address before scheduling." in page.text
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_gmail_adapter_maps_send_response_and_encodes_message() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = request.read()
        return httpx.Response(200, json={"id": "gmail-message-9"})

    adapter = GmailEmailAdapter(
        oauth=StaticOAuth(),  # type: ignore[arg-type]
        sender="recruiter@example.test",
        transport=httpx.MockTransport(handler),
    )
    result = await adapter.send(
        OutboundEmail("candidate@example.test", "Interview", "Private join link")
    )

    assert result.provider_message_id == "gmail-message-9"
    assert captured["authorization"] == "Bearer safe-access-token"
    raw_value = __import__("json").loads(captured["payload"])["raw"]
    decoded = base64.urlsafe_b64decode(raw_value + "=" * (-len(raw_value) % 4)).decode()
    assert "To: candidate@example.test" in decoded
    assert "Private join link" in decoded


@pytest.mark.asyncio
async def test_gmail_adapter_handles_missing_scope_and_configuration_safely() -> None:
    unconfigured = GmailEmailAdapter(
        oauth=GoogleOAuthClient(GoogleOAuthCredentials("", "", "")),
        sender="",
    )
    with pytest.raises(GmailConfigurationError):
        await unconfigured.send(OutboundEmail("candidate@example.test", "Interview", "Body"))

    adapter = GmailEmailAdapter(
        oauth=StaticOAuth(),  # type: ignore[arg-type]
        sender="recruiter@example.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(403, json={"error": "denied"})),
    )
    with pytest.raises(GmailAuthenticationError, match="gmail.send"):
        await adapter.send(OutboundEmail("candidate@example.test", "Interview", "Body"))
