from typing import Any

import pytest
from conftest import FakeEmbeddingAdapter, FakeLLMAdapter
from sqlalchemy.orm import Session

from app.errors import (
    InvalidJobStatusError,
    JobDescriptionValidationError,
    PolicyReviewRequiredError,
)
from app.graphs.job_description import JobDescriptionGraph
from app.graphs.job_description_enhancement import JobDescriptionEnhancementGraph
from app.repositories.company_documents import CompanyDocumentRepository
from app.repositories.jobs import JobRepository
from app.repositories.policy_knowledge import PolicyKnowledgeRepository, PolicyReviewRepository
from app.schemas.job_descriptions import JobDescriptionUpdateRequest
from app.schemas.jobs import JobWriteRequest
from app.services.job_descriptions import JobDescriptionService
from app.services.jobs import JobService
from app.services.policy_reviews import PolicyReviewService


@pytest.mark.asyncio
async def test_service_generates_edits_and_explicitly_approves_description(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    review_repository = PolicyReviewRepository(db_session)
    service = JobDescriptionService(
        repository,
        JobDescriptionGraph(
            repository,
            fake_llm_adapter,
            PolicyKnowledgeRepository(db_session),
            review_repository,
            fake_embedding_adapter,
        ),
        JobDescriptionEnhancementGraph(
            repository,
            fake_llm_adapter,
            PolicyKnowledgeRepository(db_session),
            review_repository,
            fake_embedding_adapter,
        ),
        review_repository,
    )

    generated = await service.generate_description(created.id)
    reviewed_content = (
        f"{generated.jd_content}\n\nRecruiter note: the successful candidate will own API quality."
    )
    edited = service.update_description(
        created.id,
        JobDescriptionUpdateRequest(content=reviewed_content),
    )
    with pytest.raises(PolicyReviewRequiredError):
        service.approve_description(created.id)
    review = await PolicyReviewService(
        repository,
        PolicyKnowledgeRepository(db_session),
        review_repository,
        CompanyDocumentRepository(db_session),
        fake_embedding_adapter,
        fake_llm_adapter,
        candidate_count=20,
        top_k=8,
        minimum_similarity=0.3,
    ).recheck(created.id)
    approved = service.approve_description(created.id)

    assert generated.status.value == "GENERATED"
    assert generated.jd_generated_content is not None
    assert generated.jd_content == generated.jd_generated_content
    assert generated.jd_provider == "fake"
    assert generated.jd_model == "fake-jd-model"
    assert edited.jd_content == reviewed_content
    assert edited.jd_generated_content == generated.jd_generated_content
    assert edited.jd_version == 2
    assert review.is_current is True
    assert approved.status.value == "APPROVED"
    assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_service_rejects_generation_and_approval_in_invalid_states(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
) -> None:
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    review_repository = PolicyReviewRepository(db_session)
    service = JobDescriptionService(
        repository,
        JobDescriptionGraph(
            repository,
            fake_llm_adapter,
            PolicyKnowledgeRepository(db_session),
            review_repository,
            fake_embedding_adapter,
        ),
        JobDescriptionEnhancementGraph(
            repository,
            fake_llm_adapter,
            PolicyKnowledgeRepository(db_session),
            review_repository,
            fake_embedding_adapter,
        ),
        review_repository,
    )

    with pytest.raises(InvalidJobStatusError):
        service.approve_description(created.id)

    await service.generate_description(created.id)
    with pytest.raises(InvalidJobStatusError):
        await service.generate_description(created.id)

    generated_job = repository.get(created.id)
    assert generated_job is not None
    generated_job.jd_content = None
    repository.update(generated_job)
    with pytest.raises(JobDescriptionValidationError):
        service.approve_description(created.id)
