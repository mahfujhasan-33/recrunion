from typing import Any

import pytest
from conftest import FakeLLMAdapter
from sqlalchemy.orm import Session

from app.errors import InvalidJobStatusError, JobDescriptionValidationError
from app.graphs.job_description import JobDescriptionGraph
from app.repositories.jobs import JobRepository
from app.schemas.job_descriptions import JobDescriptionUpdateRequest
from app.schemas.jobs import JobWriteRequest
from app.services.job_descriptions import JobDescriptionService
from app.services.jobs import JobService


@pytest.mark.asyncio
async def test_service_generates_edits_and_explicitly_approves_description(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
) -> None:
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    service = JobDescriptionService(
        repository,
        JobDescriptionGraph(repository, fake_llm_adapter),
    )

    generated = await service.generate_description(created.id)
    reviewed_content = (
        f"{generated.jd_content}\n\nRecruiter note: the successful candidate will own API quality."
    )
    edited = service.update_description(
        created.id,
        JobDescriptionUpdateRequest(content=reviewed_content),
    )
    approved = service.approve_description(created.id)

    assert generated.status.value == "GENERATED"
    assert generated.jd_generated_content is not None
    assert generated.jd_content == generated.jd_generated_content
    assert generated.jd_provider == "fake"
    assert generated.jd_model == "fake-jd-model"
    assert edited.jd_content == reviewed_content
    assert edited.jd_generated_content == generated.jd_generated_content
    assert edited.jd_version == 2
    assert approved.status.value == "APPROVED"
    assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_service_rejects_generation_and_approval_in_invalid_states(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
) -> None:
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    service = JobDescriptionService(
        repository,
        JobDescriptionGraph(repository, fake_llm_adapter),
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
