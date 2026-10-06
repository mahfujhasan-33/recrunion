from copy import deepcopy
from typing import Any

import pytest
from conftest import FakeLLMAdapter
from sqlalchemy.orm import Session

from app.errors import JobDescriptionValidationError, LLMProviderError
from app.graphs.job_description import JobDescriptionGraph
from app.models.jobs import JobStatus
from app.repositories.jobs import JobRepository
from app.schemas.job_descriptions import (
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
)
from app.schemas.jobs import JobWriteRequest
from app.services.jobs import JobService


@pytest.mark.asyncio
async def test_graph_executes_expected_state_progression(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
) -> None:
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))

    state = await JobDescriptionGraph(repository, fake_llm_adapter).run(created.id)

    assert state["error"] is None
    assert state["attempt_count"] == 1
    assert state["requirements"].required_skills == ["Python", "PostgreSQL"]
    assert state["generated_description"].title == "Backend Engineer"
    assert state["rendered_content"].startswith("# Backend Engineer")
    assert state["job"].status == JobStatus.GENERATED


@pytest.mark.asyncio
async def test_graph_stops_before_provider_for_invalid_requirements(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
) -> None:
    invalid_payload = deepcopy(job_payload)
    invalid_payload["selection_criteria"] = ["Must be below a specified age"]
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(invalid_payload))

    state = await JobDescriptionGraph(repository, fake_llm_adapter).run(created.id)

    assert isinstance(state["error"], JobDescriptionValidationError)
    assert fake_llm_adapter.call_count == 0
    assert repository.get(created.id).status == JobStatus.DRAFT  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_graph_retries_provider_failure_without_persisting(
    db_session: Session,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
) -> None:
    repository = JobRepository(db_session)
    created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    fake_llm_adapter.errors = [
        LLMProviderError("Provider unavailable."),
        LLMProviderError("Provider unavailable."),
    ]

    state = await JobDescriptionGraph(repository, fake_llm_adapter).run(created.id)
    persisted = repository.get(created.id)

    assert isinstance(state["error"], LLMProviderError)
    assert state["attempt_count"] == 2
    assert persisted is not None
    assert persisted.status == JobStatus.DRAFT
    assert persisted.jd_content is None


@pytest.mark.asyncio
async def test_graph_discards_invented_or_paraphrased_requirements(
    db_session: Session,
    job_payload: dict[str, Any],
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

    state = await JobDescriptionGraph(repository, InventingAdapter()).run(created.id)
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

    state = await JobDescriptionGraph(repository, UnsafeNarrativeAdapter()).run(created.id)
    persisted = repository.get(created.id)

    assert isinstance(state["error"], JobDescriptionValidationError)
    assert persisted is not None
    assert persisted.status == JobStatus.DRAFT
    assert persisted.jd_content is None
