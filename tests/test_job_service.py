from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.errors import JobNotFoundError
from app.models.jobs import JobStatus
from app.repositories.jobs import JobRepository
from app.schemas.jobs import JobWriteRequest
from app.services.jobs import JobService


def test_service_creates_draft_and_replaces_requirements_on_update(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    service = JobService(JobRepository(db_session))
    created = service.create_job(JobWriteRequest.model_validate(job_payload))

    update_payload = deepcopy(job_payload)
    update_payload["required_skills"] = ["Python", "FastAPI"]
    update_payload["preferred_skills"] = []
    updated = service.update_job(
        created.id,
        JobWriteRequest.model_validate(update_payload),
    )

    assert created.status == JobStatus.DRAFT
    assert updated.status == JobStatus.DRAFT
    assert updated.required_skills == ["Python", "FastAPI"]
    assert updated.preferred_skills == []


def test_service_raises_domain_error_for_missing_job(db_session: Session) -> None:
    service = JobService(JobRepository(db_session))

    with pytest.raises(JobNotFoundError):
        service.get_job(uuid4())
