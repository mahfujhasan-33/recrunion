from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from conftest import FakeEmbeddingAdapter, FakeLLMAdapter
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.errors import (
    CandidateScreeningEvidenceError,
    CandidateScreeningStateError,
    CandidateScreeningValidationError,
    LLMProviderError,
)
from app.graphs.candidate_screening import (
    CandidateScreeningGraph,
    build_requirements_fingerprint,
)
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
from app.models.processing_jobs import ProcessingJob, ProcessingJobStatus, ProcessingJobType
from app.models.screening import CandidateScreening, CandidateScreeningStatus
from app.repositories.candidate_processing import (
    CandidateEvidenceMatch,
    CandidateProcessingRepository,
)
from app.repositories.candidate_screening import CandidateScreeningRepository
from app.repositories.jobs import JobRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.schemas.candidate_profiles import CandidateProfileData, CandidateSkill
from app.schemas.jobs import JobWriteRequest
from app.schemas.screening import (
    CandidateScreeningEvaluation,
    GeneratedRequirementMatch,
)
from app.services.candidate_screening import CandidateScreeningService
from app.services.jobs import JobService


class FakeCandidateEvidenceRepository:
    def __init__(self, matches: list[CandidateEvidenceMatch]) -> None:
        self.matches = matches
        self.application_ids: list[UUID] = []

    def retrieve_evidence(
        self,
        application_id: UUID,
        query_embedding: list[float],
        *,
        embedding_model: str,
        top_k: int,
    ) -> list[CandidateEvidenceMatch]:
        assert len(query_embedding) == 768
        assert embedding_model == "nomic-embed-text"
        assert top_k == 4
        self.application_ids.append(application_id)
        return self.matches[:top_k]


def create_job(db_session: Session, job_payload: dict[str, Any]):
    return JobRepository(db_session).get(
        JobService(JobRepository(db_session))
        .create_job(JobWriteRequest.model_validate(job_payload))
        .id
    )


def create_ready_candidate(
    db_session: Session,
    job_id: UUID,
    *,
    reference: str,
    content: str = "Built Python APIs with PostgreSQL for production systems.",
) -> tuple[JobApplication, CandidateDocument, CandidateCVChunk]:
    candidate = Candidate(id=uuid4(), display_reference=reference)
    application = JobApplication(
        id=uuid4(),
        job_id=job_id,
        candidate_id=candidate.id,
        source=ApplicationSource.MANUAL_UPLOAD,
        status=ApplicationStatus.IMPORTED,
        candidate=candidate,
    )
    document = CandidateDocument(
        id=uuid4(),
        application_id=application.id,
        job_id=job_id,
        original_filename=f"{reference}.pdf",
        storage_key=f"JOB/{uuid4()}.pdf",
        mime_type="application/pdf",
        file_size=100,
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
        page_number=2,
        content=content,
        embedding=[1.0] + [0.0] * 767,
    )
    profile = CandidateProfile(
        id=uuid4(),
        candidate_id=candidate.id,
        application_id=application.id,
        source_document_id=document.id,
        structured_json=CandidateProfileData(
            skills=[CandidateSkill(name="Python", evidence_chunk_ids=[chunk.id])]
        ).model_dump(mode="json"),
        provider="fake",
        model="fake-profile-model",
    )
    application.document = document
    document.chunks = [chunk]
    document.profile = profile
    db_session.add(application)
    db_session.commit()
    return application, document, chunk


def build_service(
    db_session: Session,
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
    evidence_repository: FakeCandidateEvidenceRepository,
) -> CandidateScreeningService:
    repository = CandidateScreeningRepository(db_session)
    graph = CandidateScreeningGraph(
        repository,
        evidence_repository,  # type: ignore[arg-type]
        fake_llm_adapter,
        fake_embedding_adapter,
        max_attempts=1,
        retrieval_top_k=4,
        retrieval_min_similarity=0.25,
    )
    return CandidateScreeningService(
        JobRepository(db_session),
        repository,
        graph,
        worker_max_attempts=1,
    )


@pytest.mark.asyncio
async def test_screening_persists_all_statuses_and_traceable_evidence(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job = create_job(db_session, job_payload)
    assert job is not None
    application, document, chunk = create_ready_candidate(
        db_session, job.id, reference="Candidate SCREEN-A"
    )
    statuses = ["MET", "PARTIALLY_MET", *(["UNMET"] * (len(job.requirements) - 2))]
    fake_llm_adapter.screening_override = CandidateScreeningEvaluation(
        matches=[
            GeneratedRequirementMatch(
                requirement_id=requirement.id,
                status=status,
                justification="Evidence supports this classification.",
                evidence_chunk_ids=[chunk.id] if status != "UNMET" else [],
            )
            for requirement, status in zip(job.requirements, statuses, strict=True)
        ]
    )
    evidence_repository = FakeCandidateEvidenceRepository(
        [
            CandidateEvidenceMatch(
                chunk_id=chunk.id,
                document_id=document.id,
                page_number=chunk.page_number,
                content=chunk.content,
                similarity=0.91,
            )
        ]
    )
    service = build_service(
        db_session, fake_llm_adapter, fake_embedding_adapter, evidence_repository
    )
    started = service.queue(job.id, application.id)
    task_repository = ProcessingJobRepository(db_session)
    task = task_repository.claim_next()
    assert task is not None and task.id == started.task_id

    await service.process(started.screening_id, lambda _value, _message: None)
    task_repository.complete(task)

    detail = service.get_screening(job.id, application.id)
    assert detail.status == CandidateScreeningStatus.COMPLETED
    assert len(detail.matches) == len(job.requirements)
    assert {match.status.value for match in detail.matches} == {
        "MET",
        "PARTIALLY_MET",
        "UNMET",
    }
    assert detail.matches[0].evidence[0].chunk_id == chunk.id
    assert detail.matches[0].evidence[0].page_number == 2
    assert detail.matches[2].evidence == []
    assert "does not contain sufficient evidence" in detail.matches[2].justification
    assert fake_llm_adapter.screening_call_count == 1
    assert set(evidence_repository.application_ids) == {application.id}


@pytest.mark.asyncio
async def test_foreign_candidate_evidence_is_excluded_before_llm_evaluation(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job = create_job(db_session, job_payload)
    assert job is not None
    application, _, _ = create_ready_candidate(db_session, job.id, reference="Candidate SCREEN-B")
    _, foreign_document, foreign_chunk = create_ready_candidate(
        db_session, job.id, reference="Candidate SCREEN-C"
    )
    evidence_repository = FakeCandidateEvidenceRepository(
        [
            CandidateEvidenceMatch(
                chunk_id=foreign_chunk.id,
                document_id=foreign_document.id,
                page_number=1,
                content="Foreign candidate evidence must never leak.",
                similarity=0.99,
            )
        ]
    )
    service = build_service(
        db_session, fake_llm_adapter, fake_embedding_adapter, evidence_repository
    )
    started = service.queue(job.id, application.id)

    await service.process(started.screening_id, lambda _value, _message: None)

    detail = service.get_screening(job.id, application.id)
    assert all(match.status.value == "UNMET" for match in detail.matches)
    assert all(not match.evidence for match in detail.matches)


@pytest.mark.asyncio
async def test_invented_evidence_id_fails_safely_without_duplicate_results(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job = create_job(db_session, job_payload)
    assert job is not None
    application, document, chunk = create_ready_candidate(
        db_session, job.id, reference="Candidate SCREEN-D"
    )
    fake_llm_adapter.screening_override = CandidateScreeningEvaluation(
        matches=[
            GeneratedRequirementMatch(
                requirement_id=requirement.id,
                status="MET",
                justification="Invented evidence must be rejected.",
                evidence_chunk_ids=[uuid4()],
            )
            for requirement in job.requirements
        ]
    )
    service = build_service(
        db_session,
        fake_llm_adapter,
        fake_embedding_adapter,
        FakeCandidateEvidenceRepository(
            [
                CandidateEvidenceMatch(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    page_number=1,
                    content=chunk.content,
                    similarity=0.9,
                )
            ]
        ),
    )
    started = service.queue(job.id, application.id)

    with pytest.raises(CandidateScreeningEvidenceError):
        await service.process(started.screening_id, lambda _value, _message: None)

    screening = CandidateScreeningRepository(db_session).get_for_application(application.id)
    assert screening is not None
    assert screening.status == CandidateScreeningStatus.FAILED
    assert screening.matches == []
    assert screening.safe_error_code == "CANDIDATE_SCREENING_EVIDENCE_INVALID"


@pytest.mark.parametrize("status", ["MET", "PARTIALLY_MET"])
@pytest.mark.asyncio
async def test_positive_match_without_evidence_is_rejected(
    status: str,
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job = create_job(db_session, job_payload)
    assert job is not None
    application, _, _ = create_ready_candidate(
        db_session, job.id, reference=f"Candidate NO-EVIDENCE-{status}"
    )
    fake_llm_adapter.screening_override = CandidateScreeningEvaluation(
        matches=[
            GeneratedRequirementMatch(
                requirement_id=requirement.id,
                status=status,
                justification="Unsupported positive classification.",
                evidence_chunk_ids=[],
            )
            for requirement in job.requirements
        ]
    )
    service = build_service(
        db_session,
        fake_llm_adapter,
        fake_embedding_adapter,
        FakeCandidateEvidenceRepository([]),
    )
    started = service.queue(job.id, application.id)

    with pytest.raises(CandidateScreeningEvidenceError):
        await service.process(started.screening_id, lambda _value, _message: None)


@pytest.mark.asyncio
async def test_missing_requirement_result_is_rejected(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job = create_job(db_session, job_payload)
    assert job is not None
    application, _, _ = create_ready_candidate(
        db_session, job.id, reference="Candidate INCOMPLETE-RESULT"
    )
    fake_llm_adapter.screening_override = CandidateScreeningEvaluation(
        matches=[
            GeneratedRequirementMatch(
                requirement_id=job.requirements[0].id,
                status="UNMET",
                justification="No supporting evidence was found.",
                evidence_chunk_ids=[],
            )
        ]
    )
    service = build_service(
        db_session,
        fake_llm_adapter,
        fake_embedding_adapter,
        FakeCandidateEvidenceRepository([]),
    )
    started = service.queue(job.id, application.id)

    with pytest.raises(CandidateScreeningEvidenceError):
        await service.process(started.screening_id, lambda _value, _message: None)


def test_ranking_is_deterministic_and_stale_or_incomplete_results_are_unranked(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    assert job is not None
    first, first_document, _ = create_ready_candidate(
        db_session, job.id, reference="Candidate RANK-A"
    )
    second, second_document, _ = create_ready_candidate(
        db_session, job.id, reference="Candidate RANK-B"
    )
    third, third_document, _ = create_ready_candidate(
        db_session, job.id, reference="Candidate RANK-C"
    )
    fingerprint = build_requirements_fingerprint(job.requirements)
    db_session.add_all(
        [
            CandidateScreening(
                job_id=job.id,
                application_id=first.id,
                candidate_document_id=first_document.id,
                requirements_fingerprint=fingerprint,
                status=CandidateScreeningStatus.COMPLETED,
                required_met=5,
                required_partially_met=1,
                required_unmet=0,
                preferred_met=1,
            ),
            CandidateScreening(
                job_id=job.id,
                application_id=second.id,
                candidate_document_id=second_document.id,
                requirements_fingerprint=fingerprint,
                status=CandidateScreeningStatus.COMPLETED,
                required_met=5,
                required_partially_met=0,
                required_unmet=1,
                preferred_met=3,
            ),
            CandidateScreening(
                job_id=job.id,
                application_id=third.id,
                candidate_document_id=third_document.id,
                requirements_fingerprint="stale" * 12 + "dead",
                status=CandidateScreeningStatus.COMPLETED,
                required_met=99,
            ),
        ]
    )
    db_session.commit()
    service = CandidateScreeningService(
        JobRepository(db_session),
        CandidateScreeningRepository(db_session),
        SimpleNamespace(),  # type: ignore[arg-type]
        worker_max_attempts=1,
    )

    first_result = service.get_job_screening(job.id)
    second_result = service.get_job_screening(job.id)

    assert [item.application_id for item in first_result.ranked] == [first.id, second.id]
    assert [item.application_id for item in second_result.ranked] == [first.id, second.id]
    assert first_result.ranked[0].rank == 1
    assert first_result.ranked[1].rank == 2
    assert "unmet required" in first_result.ranked[1].ranking_explanation
    stale = next(item for item in first_result.unranked if item.application_id == third.id)
    assert stale.rank is None
    assert not stale.is_current
    assert "stale" in stale.ranking_explanation


def test_preferred_matches_break_required_outcome_ties(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    assert job is not None
    first, first_document, _ = create_ready_candidate(
        db_session, job.id, reference="Candidate PREF-A"
    )
    second, second_document, _ = create_ready_candidate(
        db_session, job.id, reference="Candidate PREF-B"
    )
    fingerprint = build_requirements_fingerprint(job.requirements)
    db_session.add_all(
        [
            CandidateScreening(
                job_id=job.id,
                application_id=first.id,
                candidate_document_id=first_document.id,
                requirements_fingerprint=fingerprint,
                status=CandidateScreeningStatus.COMPLETED,
                required_met=4,
                preferred_met=2,
            ),
            CandidateScreening(
                job_id=job.id,
                application_id=second.id,
                candidate_document_id=second_document.id,
                requirements_fingerprint=fingerprint,
                status=CandidateScreeningStatus.COMPLETED,
                required_met=4,
                preferred_met=1,
            ),
        ]
    )
    db_session.commit()
    service = CandidateScreeningService(
        JobRepository(db_session),
        CandidateScreeningRepository(db_session),
        SimpleNamespace(),  # type: ignore[arg-type]
        worker_max_attempts=1,
    )

    result = service.get_job_screening(job.id)

    assert [item.application_id for item in result.ranked] == [first.id, second.id]
    assert "preferred" in result.ranked[1].ranking_explanation


def test_batch_queues_only_ready_candidates_independently(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job = create_job(db_session, job_payload)
    assert job is not None
    first, first_document, first_chunk = create_ready_candidate(
        db_session, job.id, reference="Candidate BATCH-A"
    )
    second, _, _ = create_ready_candidate(db_session, job.id, reference="Candidate BATCH-B")
    ineligible, ineligible_document, _ = create_ready_candidate(
        db_session, job.id, reference="Candidate BATCH-C"
    )
    ineligible_document.processing_status = CandidateDocumentProcessingStatus.NEEDS_REVIEW
    db_session.commit()
    service = build_service(
        db_session,
        fake_llm_adapter,
        fake_embedding_adapter,
        FakeCandidateEvidenceRepository(
            [
                CandidateEvidenceMatch(
                    first_chunk.id,
                    first_document.id,
                    1,
                    first_chunk.content,
                    0.9,
                )
            ]
        ),
    )

    batch = service.queue_ready(job.id)

    assert batch.eligible == 2
    assert batch.queued == 2
    assert batch.ineligible == 1
    assert {item.application_id for item in batch.tasks} == {first.id, second.id}
    assert ineligible.id not in {item.application_id for item in batch.tasks}


def test_queue_requires_ready_cv_and_prevents_active_or_current_duplicate(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job = create_job(db_session, job_payload)
    assert job is not None
    application, document, chunk = create_ready_candidate(
        db_session, job.id, reference="Candidate QUEUE"
    )
    service = build_service(
        db_session,
        fake_llm_adapter,
        fake_embedding_adapter,
        FakeCandidateEvidenceRepository(
            [CandidateEvidenceMatch(chunk.id, document.id, 1, chunk.content, 0.9)]
        ),
    )

    started = service.queue(job.id, application.id)
    with pytest.raises(CandidateScreeningStateError):
        service.queue(job.id, application.id)

    task = ProcessingJobRepository(db_session).get(started.task_id)
    assert task is not None and task.status == ProcessingJobStatus.QUEUED
    task.status = ProcessingJobStatus.COMPLETED
    screening = CandidateScreeningRepository(db_session).get_for_application(application.id)
    assert screening is not None
    screening.status = CandidateScreeningStatus.COMPLETED
    db_session.commit()
    with pytest.raises(CandidateScreeningStateError):
        service.queue(job.id, application.id)

    screening.status = CandidateScreeningStatus.FAILED
    db_session.commit()
    retried = service.queue(job.id, application.id)
    assert retried.screening_id == started.screening_id
    assert retried.task_id != started.task_id


@pytest.mark.asyncio
async def test_retry_replaces_matches_and_provider_failure_is_safe(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    job = create_job(db_session, job_payload)
    assert job is not None
    application, document, chunk = create_ready_candidate(
        db_session, job.id, reference="Candidate RETRY"
    )
    service = build_service(
        db_session,
        fake_llm_adapter,
        fake_embedding_adapter,
        FakeCandidateEvidenceRepository(
            [CandidateEvidenceMatch(chunk.id, document.id, 1, chunk.content, 0.9)]
        ),
    )
    first = service.queue(job.id, application.id)
    first_task = ProcessingJobRepository(db_session).claim_next()
    assert first_task is not None
    await service.process(first.screening_id, lambda _value, _message: None)
    ProcessingJobRepository(db_session).complete(first_task)
    first_match_count = len(service.get_screening(job.id, application.id).matches)

    screening = CandidateScreeningRepository(db_session).get_for_application(application.id)
    assert screening is not None
    screening.status = CandidateScreeningStatus.FAILED
    db_session.commit()
    fake_llm_adapter.screening_errors.append(LLMProviderError("Provider unavailable."))
    failed = service.queue(job.id, application.id)
    failed_task = ProcessingJobRepository(db_session).claim_next()
    assert failed_task is not None and failed_task.id == failed.task_id
    with pytest.raises(LLMProviderError):
        await service.process(failed.screening_id, lambda _value, _message: None)
    ProcessingJobRepository(db_session).fail_or_retry(
        failed_task, "LLM_PROVIDER_UNAVAILABLE", "Provider unavailable."
    )
    failed_detail = service.get_screening(job.id, application.id)
    assert failed_detail.status == CandidateScreeningStatus.FAILED
    assert failed_detail.matches == []
    assert failed_detail.required is None

    retried = service.queue(job.id, application.id)
    retry_task = ProcessingJobRepository(db_session).claim_next()
    assert retry_task is not None and retry_task.id == retried.task_id
    await service.process(retried.screening_id, lambda _value, _message: None)
    ProcessingJobRepository(db_session).complete(retry_task)

    detail = service.get_screening(job.id, application.id)
    assert detail.status == CandidateScreeningStatus.COMPLETED
    assert len(detail.matches) == first_match_count == len(job.requirements)


@pytest.mark.asyncio
async def test_protected_requirement_fails_before_llm_and_is_not_ranked(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    protected_payload = {
        **job_payload,
        "selection_criteria": ["Candidate must be female"],
    }
    job = create_job(db_session, protected_payload)
    assert job is not None
    application, document, chunk = create_ready_candidate(
        db_session, job.id, reference="Candidate FAIRNESS"
    )
    service = build_service(
        db_session,
        fake_llm_adapter,
        fake_embedding_adapter,
        FakeCandidateEvidenceRepository(
            [CandidateEvidenceMatch(chunk.id, document.id, 1, chunk.content, 0.9)]
        ),
    )
    started = service.queue(job.id, application.id)

    with pytest.raises(CandidateScreeningValidationError):
        await service.process(started.screening_id, lambda _value, _message: None)

    assert fake_llm_adapter.screening_call_count == 0
    ranking = service.get_job_screening(job.id)
    assert ranking.ranked == []
    assert ranking.unranked[0].screening_status == CandidateScreeningStatus.FAILED


def test_screening_api_and_page_expose_f1_workflow(
    client: TestClient,
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = client.post("/api/v1/jobs", json=job_payload).json()
    application, _, _ = create_ready_candidate(
        db_session, UUID(job["id"]), reference="Candidate API"
    )

    started = client.post(f"/api/v1/jobs/{job['id']}/applications/{application.id}/screen")

    assert started.status_code == 202
    assert started.json()["status"] == "QUEUED"
    ranking = client.get(f"/api/v1/jobs/{job['id']}/screening")
    assert ranking.status_code == 200
    assert ranking.json()["unranked"][0]["screening_status"] == "QUEUED"
    assert (
        client.get(f"/api/v1/jobs/{job['id']}/applications/{uuid4()}/screening").status_code == 404
    )
    page = client.get(f"/jobs/{job['id']}/applications")
    assert "Screen ready candidates" in page.text
    assert "Candidate screening and ranking" in page.text


def test_candidate_repository_query_is_explicitly_application_scoped() -> None:
    source = CandidateProcessingRepository.retrieve_evidence.__code__
    assert "application_id" in source.co_varnames


def test_processing_job_column_accepts_candidate_screening_type() -> None:
    column_length = ProcessingJob.__table__.c.job_type.type.length
    assert column_length is not None
    assert column_length >= len(ProcessingJobType.SCREEN_CANDIDATE_APPLICATION.value)
