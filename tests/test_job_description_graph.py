from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from conftest import FakeEmbeddingAdapter, FakeLLMAdapter
from sqlalchemy.orm import Session

from app.errors import JobDescriptionValidationError, LLMProviderError
from app.graphs.job_description import JobDescriptionGraph
from app.graphs.job_description_enhancement import JobDescriptionEnhancementGraph
from app.models.jobs import JobStatus
from app.repositories.jobs import JobRepository
from app.repositories.policy_knowledge import PolicyKnowledgeRepository, PolicyReviewRepository
from app.schemas.job_descriptions import (
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
    PolicyAlignmentRequest,
    PolicyAlignmentResult,
)
from app.schemas.jobs import JobWriteRequest
from app.schemas.policy_findings import (
    GeneratedPolicyFinding,
    PolicyAlignmentEvaluation,
    PolicyEvidence,
)
from app.services.jobs import JobService


@pytest.mark.asyncio
async def test_graph_executes_expected_state_progression(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))

    state = await build_graph(db_session, repository, fake_llm_adapter, fake_embedding_adapter).run(
        created.id
    )

    assert state["error"] is None
    assert state["generation_attempt_count"] == 1
    assert state["requirements"].required_skills == ["Python", "PostgreSQL"]
    assert state["generated_description"].title == "Backend Engineer"
    assert state["rendered_content"].startswith("# Backend Engineer")
    assert state["job"].status == JobStatus.GENERATED


@pytest.mark.asyncio
async def test_graph_stops_before_provider_for_invalid_requirements(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    invalid_payload = deepcopy(job_payload)
    invalid_payload["selection_criteria"] = ["Must be below a specified age"]
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(invalid_payload))

    state = await build_graph(db_session, repository, fake_llm_adapter, fake_embedding_adapter).run(
        created.id
    )

    assert isinstance(state["error"], JobDescriptionValidationError)
    assert fake_llm_adapter.call_count == 0
    assert repository.get(created.id).status == JobStatus.DRAFT  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_graph_retries_provider_failure_without_persisting(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    fake_llm_adapter.errors = [
        LLMProviderError("Provider unavailable."),
        LLMProviderError("Provider unavailable."),
    ]

    state = await build_graph(db_session, repository, fake_llm_adapter, fake_embedding_adapter).run(
        created.id
    )
    persisted = repository.get(created.id)

    assert isinstance(state["error"], LLMProviderError)
    assert state["generation_attempt_count"] == 2
    assert persisted is not None
    assert persisted.status == JobStatus.DRAFT
    assert persisted.jd_content is None


@pytest.mark.asyncio
async def test_graph_discards_invented_or_paraphrased_requirements(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    class InventingAdapter(FakeLLMAdapter):
        async def generate_job_description(
            self,
            request: JobDescriptionGenerationRequest,
        ) -> JobDescriptionGenerationResult:
            result = await super().generate_job_description(request)
            result.description.title = "Paraphrased role title"
            result.description.qualifications = ["An altered qualification"]
            result.description.minimum_experience = 10
            result.description.application_information = "Apply through another channel."
            result.description.required_skills.append("Invented mandatory certification")
            return result

    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))

    state = await build_graph(
        db_session, repository, InventingAdapter(), fake_embedding_adapter
    ).run(created.id)
    persisted = repository.get(created.id)

    assert state["error"] is None
    assert state["generated_description"].title == job_payload["title"]
    assert state["generated_description"].required_skills == job_payload["required_skills"]
    assert state["generated_description"].qualifications == job_payload["qualifications"]
    assert state["generated_description"].minimum_experience == job_payload["minimum_experience"]
    assert (
        str(job_payload["application_email"])
        in state["generated_description"].application_information
    )
    assert persisted is not None
    assert persisted.status == JobStatus.GENERATED
    assert "Invented mandatory certification" not in persisted.jd_content


@pytest.mark.asyncio
async def test_graph_rejects_protected_language_in_generated_narrative(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    class UnsafeNarrativeAdapter(FakeLLMAdapter):
        async def generate_job_description(
            self,
            request: JobDescriptionGenerationRequest,
        ) -> JobDescriptionGenerationResult:
            result = await super().generate_job_description(request)
            result.description.responsibilities = ["Choose candidates based on their religion."]
            return result

    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))

    state = await build_graph(
        db_session, repository, UnsafeNarrativeAdapter(), fake_embedding_adapter
    ).run(created.id)
    persisted = repository.get(created.id)

    assert isinstance(state["error"], JobDescriptionValidationError)
    assert persisted is not None
    assert persisted.status == JobStatus.DRAFT
    assert persisted.jd_content is None


def build_graph(
    session: Session,
    repository: JobRepository,
    llm_adapter: FakeLLMAdapter,
    embedding_adapter: FakeEmbeddingAdapter,
) -> JobDescriptionGraph:
    return JobDescriptionGraph(
        repository,
        llm_adapter,
        PolicyKnowledgeRepository(session),
        PolicyReviewRepository(session),
        embedding_adapter,
    )


@pytest.mark.asyncio
async def test_graph_persists_traceable_policy_findings(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    evidence = PolicyEvidence(
        evidence_id=uuid4(),
        document_id=uuid4(),
        document_filename="hiring-policy.txt",
        document_type="HIRING_POLICY",
        document_checksum="a" * 64,
        content="Every job description must state a clear application process.",
        page_number=1,
        similarity=0.9,
    )

    class FakePolicyRepository:
        def has_ready_documents(self, embedding_model: str) -> bool:
            return True

        def retrieve(self, *args: object, **kwargs: object) -> list[PolicyEvidence]:
            return [evidence]

    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    review_repository = PolicyReviewRepository(db_session)
    graph = JobDescriptionGraph(
        repository,
        fake_llm_adapter,
        FakePolicyRepository(),  # type: ignore[arg-type]
        review_repository,
        fake_embedding_adapter,
    )

    state = await graph.run(created.id)
    review = review_repository.latest(created.id)

    assert state["error"] is None
    assert review is not None
    assert review.retrieval_count == 1
    assert review.findings[0].source_chunk_id == evidence.evidence_id
    assert review.findings[0].evidence_excerpt == evidence.content


@pytest.mark.asyncio
async def test_graph_retries_policy_provider_failure(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    evidence = PolicyEvidence(
        evidence_id=uuid4(),
        document_id=uuid4(),
        document_filename="policy.txt",
        document_type="HIRING_POLICY",
        document_checksum="b" * 64,
        content="State the application process.",
        similarity=0.9,
    )

    class FakePolicyRepository:
        def has_ready_documents(self, embedding_model: str) -> bool:
            return True

        def retrieve(self, *args: object, **kwargs: object) -> list[PolicyEvidence]:
            return [evidence]

    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    fake_llm_adapter.policy_errors = [LLMProviderError("Provider unavailable.")]
    graph = JobDescriptionGraph(
        repository,
        fake_llm_adapter,
        FakePolicyRepository(),  # type: ignore[arg-type]
        PolicyReviewRepository(db_session),
        fake_embedding_adapter,
        max_attempts=2,
    )

    state = await graph.run(created.id)

    assert state["error"] is None
    assert state["policy_attempt_count"] == 2
    assert fake_llm_adapter.policy_call_count == 2


@pytest.mark.asyncio
async def test_enhancement_graph_applies_findings_and_persists_new_review(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    evidence = PolicyEvidence(
        evidence_id=uuid4(),
        document_id=uuid4(),
        document_filename="jd-standard.txt",
        document_type="JOB_DESCRIPTION_STANDARD",
        document_checksum="c" * 64,
        content="Every job description must explain the equal-opportunity policy.",
        similarity=0.95,
    )

    class EnhancementPolicyRepository:
        def has_ready_documents(self, embedding_model: str) -> bool:
            return True

        def retrieve(self, *args: object, **kwargs: object) -> list[PolicyEvidence]:
            return [evidence]

    class EnhancementAdapter(FakeLLMAdapter):
        async def evaluate_policy_alignment(
            self,
            request: PolicyAlignmentRequest,
        ) -> PolicyAlignmentResult:
            self.policy_call_count += 1
            status = "PARTIALLY_MET" if self.policy_call_count == 1 else "MET"
            return PolicyAlignmentResult(
                evaluation=PolicyAlignmentEvaluation(
                    findings=[
                        GeneratedPolicyFinding(
                            evidence_id=evidence.evidence_id,
                            related_jd_section="SUMMARY",
                            status=status,
                            explanation="The policy needs explicit language.",
                        )
                    ]
                ),
                provider="fake",
                model="fake-jd-model",
            )

    adapter = EnhancementAdapter()
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    review_repository = PolicyReviewRepository(db_session)
    generated_state = await JobDescriptionGraph(
        repository,
        adapter,
        PolicyKnowledgeRepository(db_session),
        review_repository,
        fake_embedding_adapter,
    ).run(created.id)
    original_content = generated_state["job"].jd_content

    state = await JobDescriptionEnhancementGraph(
        repository,
        adapter,
        EnhancementPolicyRepository(),  # type: ignore[arg-type]
        review_repository,
        fake_embedding_adapter,
    ).run(created.id)
    review = review_repository.latest(created.id)

    assert state["error"] is None
    assert state["job"].jd_version == 2
    assert state["job"].jd_content != original_content
    assert "- Python" in state["job"].jd_content
    assert adapter.enhancement_call_count == 1
    assert adapter.policy_call_count == 2
    assert review is not None
    assert review.jd_version == 2
    assert review.findings[0].status.value == "MET"
