from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from conftest import FakeEmbeddingAdapter, FakeLLMAdapter
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.document_storage import LocalDocumentStorage
from app.errors import (
    CandidateDocumentValidationError,
    CandidateProcessingStateError,
    CandidateProfileValidationError,
    EmbeddingProviderError,
    LLMProviderError,
)
from app.models.applications import (
    CandidateCVChunk,
    CandidateDocumentProcessingStatus,
    CandidateProfile,
)
from app.repositories.applications import ApplicationRepository
from app.repositories.candidate_processing import CandidateProcessingRepository
from app.repositories.jobs import JobRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.schemas.candidate_profiles import CandidateProfileData, CandidateSkill
from app.schemas.jobs import JobWriteRequest
from app.services.application_intake import ApplicationIntakeService, IncomingCandidateDocument
from app.services.candidate_pdf_extraction import CandidatePDFExtractor
from app.services.candidate_processing import CandidateProcessingService
from app.services.jobs import JobService


def synthetic_text_pdf(*pages: str) -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 10 Tf 72 720 Td ({escaped}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(output)
    return output.getvalue()


def create_imported_document(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
    content: bytes,
) -> tuple[UUID, UUID, UUID, LocalDocumentStorage]:
    job = JobService(JobRepository(db_session)).create_job(
        JobWriteRequest.model_validate(job_payload)
    )
    storage = LocalDocumentStorage(tmp_path, 10 * 1024 * 1024)
    intake = ApplicationIntakeService(
        JobRepository(db_session),
        ApplicationRepository(db_session),
        storage,
        max_file_size_bytes=10 * 1024 * 1024,
    )
    result = intake.upload_batch(
        job.id,
        [IncomingCandidateDocument("synthetic-candidate.pdf", "application/pdf", BytesIO(content))],
    )
    assert result.imported == 1
    application = result.files[0].application
    assert application is not None
    persisted = ApplicationRepository(db_session).get_for_job(job.id, application.id)
    assert persisted is not None
    return job.id, application.id, persisted.document.id, storage


def build_processing_service(
    db_session: Session,
    storage: LocalDocumentStorage,
    fake_llm_adapter: FakeLLMAdapter,
    embedding_adapter: FakeEmbeddingAdapter | object,
    *,
    minimum_text_characters: int = 40,
    worker_max_attempts: int = 1,
) -> CandidateProcessingService:
    return CandidateProcessingService(
        JobRepository(db_session),
        CandidateProcessingRepository(db_session),
        storage,
        CandidatePDFExtractor(),
        fake_llm_adapter,
        embedding_adapter,
        worker_max_attempts=worker_max_attempts,
        minimum_text_characters=minimum_text_characters,
    )


def test_pdf_extraction_preserves_page_numbers_and_rejects_encryption() -> None:
    extractor = CandidatePDFExtractor()
    pages = extractor.extract(BytesIO(synthetic_text_pdf("First page", "Second page")))

    assert [(page.page_number, page.text) for page in pages] == [
        (1, "First page"),
        (2, "Second page"),
    ]

    encrypted = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("synthetic-password")
    writer.write(encrypted)
    with pytest.raises(CandidateDocumentValidationError):
        extractor.extract(BytesIO(encrypted.getvalue()))
    with pytest.raises(CandidateDocumentValidationError):
        extractor.extract(BytesIO(b"%PDF-malformed"))


@pytest.mark.asyncio
async def test_processing_persists_profile_chunks_vectors_and_traceability(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    content = synthetic_text_pdf(
        "Synthetic Candidate email synthetic@example.test. BSc Computer Science at Synthetic "
        "University. Software Engineer at Example Systems. Python FastAPI PostgreSQL. Built a "
        "recruitment platform project and holds a cloud certification."
    )
    job_id, application_id, document_id, storage = create_imported_document(
        db_session, tmp_path, job_payload, content
    )
    service = build_processing_service(
        db_session,
        storage,
        fake_llm_adapter,
        fake_embedding_adapter,
    )
    progress: list[tuple[int, str]] = []

    started = service.queue(job_id, application_id)
    task_repository = ProcessingJobRepository(db_session)
    task = task_repository.claim_next()
    assert task is not None and task.id == started.task_id
    await service.process(document_id, lambda value, message: progress.append((value, message)))
    task_repository.complete(task)

    document = CandidateProcessingRepository(db_session).get_document(document_id)
    assert document is not None
    assert document.processing_status == CandidateDocumentProcessingStatus.READY
    assert document.embedding_model == "nomic-embed-text"
    assert document.embedding_dimension == 768
    assert document.chunk_count == len(document.chunks) > 0
    assert all(chunk.page_number == 1 for chunk in document.chunks)
    assert all(len(chunk.embedding or []) == 768 for chunk in document.chunks)
    assert document.profile is not None
    profile = CandidateProfileData.model_validate(document.profile.structured_json)
    assert profile.education[0].qualification == "BSc Computer Science"
    assert profile.work_history[0].role == "Software Engineer"
    assert profile.skills[0].name == "Python"
    assert profile.certifications[0].name == "Synthetic Cloud Certification"
    assert profile.projects[0].name == "Synthetic platform"
    chunk_ids = {chunk.id for chunk in document.chunks}
    assert set(profile.skills[0].evidence_chunk_ids).issubset(chunk_ids)
    assert progress[0][0] == 10
    assert progress[-1] == (95, "Candidate profile and evidence are ready")
    detail = ApplicationIntakeService(
        JobRepository(db_session),
        ApplicationRepository(db_session),
        storage,
        max_file_size_bytes=10 * 1024 * 1024,
    ).get_application(job_id, application_id)
    assert detail.candidate_profile is not None
    assert detail.candidate_profile.profile.skills[0].name == "Python"
    assert {evidence.id for evidence in detail.candidate_profile.evidence} == set(
        profile.skills[0].evidence_chunk_ids
    )


@pytest.mark.asyncio
async def test_text_poor_pdf_needs_review_without_profile_or_chunks(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job_id, application_id, document_id, storage = create_imported_document(
        db_session,
        tmp_path,
        job_payload,
        synthetic_text_pdf("image"),
    )
    service = build_processing_service(
        db_session,
        storage,
        fake_llm_adapter,
        fake_embedding_adapter,
        minimum_text_characters=40,
    )

    service.queue(job_id, application_id)
    await service.process(document_id, lambda _progress, _message: None)

    document = CandidateProcessingRepository(db_session).get_document(document_id)
    assert document is not None
    assert document.processing_status == CandidateDocumentProcessingStatus.NEEDS_REVIEW
    assert document.safe_error_code == "INSUFFICIENT_EXTRACTABLE_TEXT"
    assert document.chunks == []
    assert document.profile is None
    assert fake_llm_adapter.profile_call_count == 0


@pytest.mark.asyncio
async def test_invalid_profile_evidence_is_rejected_and_chunks_survive_failure(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    _, _, document_id, storage = create_imported_document(
        db_session,
        tmp_path,
        job_payload,
        synthetic_text_pdf("Python engineer with substantial synthetic project evidence. " * 5),
    )
    fake_llm_adapter.profile_override = CandidateProfileData(
        skills=[CandidateSkill(name="Python", evidence_chunk_ids=[uuid4()])]
    )
    service = build_processing_service(
        db_session,
        storage,
        fake_llm_adapter,
        fake_embedding_adapter,
    )

    with pytest.raises(CandidateProfileValidationError):
        await service.process(document_id, lambda _progress, _message: None)

    document = CandidateProcessingRepository(db_session).get_document(document_id)
    assert document is not None
    assert document.processing_status == CandidateDocumentProcessingStatus.FAILED
    assert document.profile is None
    assert document.chunks
    assert all(chunk.embedding is None for chunk in document.chunks)


@pytest.mark.asyncio
async def test_llm_and_embedding_failures_are_safe_and_retry_replaces_derived_data(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job_id, application_id, document_id, storage = create_imported_document(
        db_session,
        tmp_path,
        job_payload,
        synthetic_text_pdf("Python engineer with synthetic education and project evidence. " * 8),
    )
    fake_llm_adapter.profile_errors.append(LLMProviderError("Provider unavailable."))
    service = build_processing_service(
        db_session,
        storage,
        fake_llm_adapter,
        fake_embedding_adapter,
    )

    started = service.queue(job_id, application_id)
    task_repository = ProcessingJobRepository(db_session)
    task = task_repository.claim_next()
    assert task is not None and task.id == started.task_id
    with pytest.raises(LLMProviderError):
        await service.process(document_id, lambda _progress, _message: None)
    task_repository.fail_or_retry(task, "LLM_PROVIDER_UNAVAILABLE", "Provider unavailable.")
    first_chunk_count = db_session.scalar(select(func.count()).select_from(CandidateCVChunk))
    assert first_chunk_count

    retried = service.queue(job_id, application_id)
    retry_task = task_repository.claim_next()
    assert retry_task is not None and retry_task.id == retried.task_id
    await service.process(document_id, lambda _progress, _message: None)
    task_repository.complete(retry_task)

    assert (
        db_session.scalar(select(func.count()).select_from(CandidateCVChunk)) == first_chunk_count
    )
    assert db_session.scalar(select(func.count()).select_from(CandidateProfile)) == 1

    class FailingEmbeddingAdapter(FakeEmbeddingAdapter):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise EmbeddingProviderError("Embedding unavailable.")

    second_job_payload = {**job_payload, "title": "Synthetic Data Engineer"}
    _, _, second_document_id, second_storage = create_imported_document(
        db_session,
        tmp_path / "second",
        second_job_payload,
        synthetic_text_pdf("Data engineer with Python and PostgreSQL evidence. " * 8),
    )
    failing_service = build_processing_service(
        db_session,
        second_storage,
        fake_llm_adapter,
        FailingEmbeddingAdapter(),
    )
    with pytest.raises(EmbeddingProviderError):
        await failing_service.process(second_document_id, lambda _progress, _message: None)
    second_document = CandidateProcessingRepository(db_session).get_document(second_document_id)
    assert second_document is not None
    assert second_document.processing_status == CandidateDocumentProcessingStatus.FAILED
    assert second_document.safe_error_code == "EMBEDDING_PROVIDER_UNAVAILABLE"


def test_api_queues_single_and_pending_processing_and_blocks_duplicate(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    job = client.post("/api/v1/jobs", json=job_payload).json()
    content = synthetic_text_pdf("Synthetic Python candidate evidence. " * 8)
    uploaded = client.post(
        f"/api/v1/jobs/{job['id']}/applications/upload",
        files=[("files", ("candidate.pdf", content, "application/pdf"))],
    ).json()
    application = uploaded["files"][0]["application"]

    started = client.post(f"/api/v1/jobs/{job['id']}/applications/{application['id']}/process")

    assert started.status_code == 202
    assert started.json()["processing_status"] == "QUEUED"
    duplicate = client.post(f"/api/v1/jobs/{job['id']}/applications/{application['id']}/process")
    assert duplicate.status_code == 409
    detail = client.get(f"/api/v1/jobs/{job['id']}/applications/{application['id']}").json()
    assert detail["processing_status"] == "QUEUED"
    assert detail["processing_task_id"] == started.json()["task_id"]
    assert detail["candidate_profile"] is None
    assert client.post(f"/api/v1/jobs/{uuid4()}/applications/process-pending").status_code == 404
    assert client.get(f"/jobs/{job['id']}/applications").status_code == 200
    assert "Process pending CVs" in client.get(f"/jobs/{job['id']}/applications").text


def test_process_pending_queues_each_imported_document(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    job = client.post("/api/v1/jobs", json=job_payload).json()
    client.post(
        f"/api/v1/jobs/{job['id']}/applications/upload",
        files=[
            (
                "files",
                (
                    "one.pdf",
                    synthetic_text_pdf("First candidate evidence. " * 8),
                    "application/pdf",
                ),
            ),
            (
                "files",
                (
                    "two.pdf",
                    synthetic_text_pdf("Second candidate evidence. " * 8),
                    "application/pdf",
                ),
            ),
        ],
    )

    response = client.post(f"/api/v1/jobs/{job['id']}/applications/process-pending")

    assert response.status_code == 202
    assert response.json()["queued"] == 2
    assert len({task["task_id"] for task in response.json()["tasks"]}) == 2
    assert (
        client.post(f"/api/v1/jobs/{job['id']}/applications/process-pending").json()["queued"] == 0
    )


def test_ready_document_cannot_be_queued_again(
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job_id, application_id, document_id, storage = create_imported_document(
        db_session,
        tmp_path,
        job_payload,
        synthetic_text_pdf("Synthetic candidate evidence. " * 8),
    )
    document = CandidateProcessingRepository(db_session).get_document(document_id)
    assert document is not None
    document.processing_status = CandidateDocumentProcessingStatus.READY
    db_session.commit()
    service = build_processing_service(
        db_session,
        storage,
        fake_llm_adapter,
        fake_embedding_adapter,
    )

    with pytest.raises(CandidateProcessingStateError):
        service.queue(job_id, application_id)


def test_candidate_vector_schema_uses_existing_nomic_dimension() -> None:
    assert CandidateCVChunk.__table__.c.embedding.type.dim == 768


def test_candidate_profile_schema_rejects_protected_characteristic_fields() -> None:
    with pytest.raises(ValidationError):
        CandidateProfileData.model_validate(
            {
                "skills": [
                    {
                        "name": "Python",
                        "evidence_chunk_ids": [str(uuid4())],
                        "gender": "not permitted",
                    }
                ]
            }
        )
