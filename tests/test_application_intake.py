import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.adapters.document_storage import LocalDocumentStorage
from app.errors import DocumentStorageValidationError
from app.models.applications import Candidate, CandidateDocument, JobApplication
from app.repositories.applications import ApplicationRepository
from app.repositories.jobs import JobRepository
from app.schemas.applications import ApplicationUploadOutcome
from app.schemas.jobs import JobWriteRequest
from app.services.application_intake import ApplicationIntakeService, IncomingCandidateDocument
from app.services.jobs import JobService


def synthetic_pdf(title: str = "Synthetic candidate") -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": title})
    writer.write(output)
    return output.getvalue()


def build_service(
    db_session: Session,
    root: Path,
    *,
    max_size: int = 1024 * 1024,
) -> ApplicationIntakeService:
    return ApplicationIntakeService(
        JobRepository(db_session),
        ApplicationRepository(db_session),
        LocalDocumentStorage(root, max_size),
        max_file_size_bytes=max_size,
    )


def create_job(db_session: Session, job_payload: dict[str, Any]):
    return JobService(JobRepository(db_session)).create_job(
        JobWriteRequest.model_validate(job_payload)
    )


def upload(filename: str, content: bytes, content_type: str = "application/pdf"):
    return IncomingCandidateDocument(filename, content_type, BytesIO(content))


@pytest.mark.parametrize(
    ("incoming", "expected_message"),
    [
        (upload("candidate.txt", synthetic_pdf()), "Only PDF"),
        (upload("candidate.pdf", b"not a pdf"), "not a valid PDF"),
        (upload("candidate.pdf", b""), "empty"),
        (upload("candidate.pdf", synthetic_pdf(), "application/octet-stream"), "content type"),
    ],
)
def test_invalid_candidate_files_are_reported_per_file(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
    incoming: IncomingCandidateDocument,
    expected_message: str,
) -> None:
    job = create_job(db_session, job_payload)

    result = build_service(db_session, tmp_path).upload_batch(job.id, [incoming])

    assert result.received == 1
    assert result.invalid == 1
    assert result.imported == 0
    assert expected_message in result.files[0].message
    assert list(tmp_path.rglob("*.pdf")) == []


def test_oversized_candidate_file_is_rejected(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    content = synthetic_pdf()

    result = build_service(db_session, tmp_path, max_size=len(content) - 1).upload_batch(
        job.id,
        [upload("candidate.pdf", content)],
    )

    assert result.invalid == 1
    assert "upload limit" in result.files[0].message


def test_batch_persists_candidates_applications_documents_and_isolates_invalid_files(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    service = build_service(db_session, tmp_path)

    result = service.upload_batch(
        job.id,
        [
            upload("../../candidate-one.pdf", synthetic_pdf("Candidate one")),
            upload("candidate-two.pdf", synthetic_pdf("Candidate two")),
            upload("fake.pdf", b"fake"),
        ],
    )

    assert result.model_dump(
        include={"received", "imported", "duplicates", "invalid", "failed"}
    ) == {
        "received": 3,
        "imported": 2,
        "duplicates": 0,
        "invalid": 1,
        "failed": 0,
    }
    assert db_session.scalar(select(func.count()).select_from(Candidate)) == 2
    assert db_session.scalar(select(func.count()).select_from(JobApplication)) == 2
    assert db_session.scalar(select(func.count()).select_from(CandidateDocument)) == 2
    documents = list(db_session.scalars(select(CandidateDocument)))
    assert {document.original_filename for document in documents} == {
        "candidate-one.pdf",
        "candidate-two.pdf",
    }
    assert all(document.storage_key.startswith(f"{job.code}/") for document in documents)
    assert all(
        Path(document.storage_key).name != document.original_filename for document in documents
    )
    assert all((tmp_path / Path(document.storage_key)).is_file() for document in documents)
    expected_hashes = {
        hashlib.sha256(synthetic_pdf("Candidate one")).hexdigest(),
        hashlib.sha256(synthetic_pdf("Candidate two")).hexdigest(),
    }
    assert {document.sha256 for document in documents} == expected_hashes
    assert all(document.job_id == job.id for document in documents)
    assert all(document.application.source.value == "MANUAL_UPLOAD" for document in documents)


def test_duplicate_is_scoped_to_job_and_repeated_upload_is_idempotent(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
) -> None:
    first_job = create_job(db_session, job_payload)
    second_payload = {**job_payload, "title": "Platform Engineer"}
    second_job = create_job(db_session, second_payload)
    service = build_service(db_session, tmp_path)
    content = synthetic_pdf("Reusable synthetic CV")

    first = service.upload_batch(first_job.id, [upload("candidate.pdf", content)])
    duplicate = service.upload_batch(first_job.id, [upload("renamed.pdf", content)])
    other_job = service.upload_batch(second_job.id, [upload("candidate.pdf", content)])

    assert first.imported == 1
    assert duplicate.duplicates == 1
    assert duplicate.files[0].outcome == ApplicationUploadOutcome.SKIPPED_DUPLICATE
    assert other_job.imported == 1
    assert db_session.scalar(select(func.count()).select_from(CandidateDocument)) == 2


def test_storage_failure_for_one_file_does_not_abort_later_files(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
) -> None:
    class FailOnceStorage:
        def __init__(self) -> None:
            self.delegate = LocalDocumentStorage(tmp_path, 1024 * 1024)
            self.failed = False

        def save(self, source, suffix, *, namespace=None):
            if not self.failed:
                self.failed = True
                raise OSError("simulated storage failure")
            return self.delegate.save(source, suffix, namespace=namespace)

        def open(self, storage_key):
            return self.delegate.open(storage_key)

        def delete(self, storage_key):
            self.delegate.delete(storage_key)

    job = create_job(db_session, job_payload)
    service = ApplicationIntakeService(
        JobRepository(db_session),
        ApplicationRepository(db_session),
        FailOnceStorage(),
        max_file_size_bytes=1024 * 1024,
    )

    result = service.upload_batch(
        job.id,
        [
            upload("first.pdf", synthetic_pdf("First")),
            upload("second.pdf", synthetic_pdf("Second")),
        ],
    )

    assert result.failed == 1
    assert result.imported == 1


def test_new_file_is_removed_when_database_persistence_fails(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
) -> None:
    class FailingApplicationRepository(ApplicationRepository):
        def create(self, candidate, application, document):
            raise SQLAlchemyError("simulated persistence failure")

    job = create_job(db_session, job_payload)
    service = ApplicationIntakeService(
        JobRepository(db_session),
        FailingApplicationRepository(db_session),
        LocalDocumentStorage(tmp_path, 1024 * 1024),
        max_file_size_bytes=1024 * 1024,
    )

    result = service.upload_batch(job.id, [upload("candidate.pdf", synthetic_pdf())])

    assert result.failed == 1
    assert list(tmp_path.rglob("*.pdf")) == []


def test_local_storage_rejects_path_traversal(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path, 1024 * 1024)
    content = synthetic_pdf()
    stored = storage.save(BytesIO(content), ".pdf", namespace="JOB-SYNTHETIC")

    with storage.open(stored.storage_key) as stored_file:
        assert stored_file.read() == content

    with pytest.raises(DocumentStorageValidationError):
        storage.save(BytesIO(synthetic_pdf()), ".pdf", namespace="../outside")
    with pytest.raises(DocumentStorageValidationError):
        storage.open("../outside.pdf")


def test_application_upload_list_detail_api_and_page(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    job = client.post("/api/v1/jobs", json=job_payload).json()
    first_pdf = synthetic_pdf("API candidate one")
    second_pdf = synthetic_pdf("API candidate two")

    response = client.post(
        f"/api/v1/jobs/{job['id']}/applications/upload",
        files=[
            ("files", ("candidate-one.pdf", first_pdf, "application/pdf")),
            ("files", ("candidate-two.pdf", second_pdf, "application/pdf")),
            ("files", ("invalid.pdf", b"not a pdf", "application/pdf")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 3
    assert body["imported"] == 2
    assert body["invalid"] == 1
    applications = client.get(f"/api/v1/jobs/{job['id']}/applications").json()
    assert len(applications) == 2
    assert "storage_key" not in applications[0]
    assert "sha256" not in applications[0]
    detail = client.get(f"/api/v1/jobs/{job['id']}/applications/{applications[0]['id']}")
    assert detail.status_code == 200
    page = client.get(f"/jobs/{job['id']}/applications")
    assert page.status_code == 200
    assert "Upload CVs" in page.text
    assert "candidate-one.pdf" in page.text


def test_application_api_handles_duplicates_and_missing_resources(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    job = client.post("/api/v1/jobs", json=job_payload).json()
    content = synthetic_pdf("Duplicate candidate")
    endpoint = f"/api/v1/jobs/{job['id']}/applications/upload"

    assert (
        client.post(
            endpoint,
            files=[("files", ("candidate.pdf", content, "application/pdf"))],
        ).json()["imported"]
        == 1
    )
    assert (
        client.post(
            endpoint,
            files=[("files", ("candidate-copy.pdf", content, "application/pdf"))],
        ).json()["duplicates"]
        == 1
    )
    assert client.get(f"/api/v1/jobs/{uuid4()}/applications").status_code == 404
    assert (
        client.post(
            f"/api/v1/jobs/{uuid4()}/applications/upload",
            files=[("files", ("candidate.pdf", content, "application/pdf"))],
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/jobs/{job['id']}/applications/{uuid4()}").status_code == 404


def test_runtime_candidate_files_are_gitignored() -> None:
    ignore_rules = Path(".gitignore").read_text(encoding="utf-8")

    assert "data/applications/*" in ignore_rules
    assert "!data/applications/.gitkeep" in ignore_rules
