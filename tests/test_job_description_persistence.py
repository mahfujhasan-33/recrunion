from typing import Any

import pytest
from conftest import FakeLLMAdapter
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.graphs.job_description import JobDescriptionGraph
from app.models.jobs import JobStatus
from app.repositories.jobs import JobRepository
from app.schemas.job_descriptions import JobDescriptionUpdateRequest
from app.schemas.jobs import JobWriteRequest
from app.services.job_descriptions import JobDescriptionService
from app.services.jobs import JobService


@pytest.mark.asyncio
async def test_generated_edited_and_approved_fields_survive_new_sessions(
    database_engine: Engine,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
) -> None:
    with Session(database_engine, expire_on_commit=False) as first_session:
        repository = JobRepository(first_session)
        created = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
        service = JobDescriptionService(
            repository,
            JobDescriptionGraph(repository, fake_llm_adapter),
        )
        generated = await service.generate_description(created.id)
        edited_content = generated.jd_content + "\n\nRecruiter-reviewed final wording."
        service.update_description(
            created.id,
            JobDescriptionUpdateRequest(content=edited_content),
        )
        service.approve_description(created.id)

    with Session(database_engine, expire_on_commit=False) as second_session:
        persisted = JobRepository(second_session).get(created.id)

        assert persisted is not None
        assert persisted.status == JobStatus.APPROVED
        assert persisted.jd_generated_content != persisted.jd_content
        assert persisted.jd_content == edited_content
        assert persisted.jd_generated_at is not None
        assert persisted.approved_at is not None
        assert persisted.jd_provider == "fake"
        assert persisted.jd_model == "fake-jd-model"
        assert persisted.jd_generation_metadata == {
            "finish_reason": "STOP",
            "input_tokens": 100,
            "output_tokens": 200,
            "total_tokens": 300,
        }
