from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.models.jobs import (
    EmploymentType,
    Job,
    JobRequirement,
    JobStatus,
    RequirementCategory,
    RequirementType,
)
from app.repositories.jobs import JobRepository


def test_repository_persists_job_and_requirements(database_engine: Engine) -> None:
    job_id = uuid4()
    job = Job(
        id=job_id,
        code=f"JOB-{job_id.hex[:8].upper()}",
        title="Data Engineer",
        location="Remote",
        employment_type=EmploymentType.CONTRACT,
        application_email="data@example.com",
        status=JobStatus.DRAFT,
        requirements=[
            JobRequirement(
                category=RequirementCategory.SKILL,
                text="Python",
                requirement_type=RequirementType.REQUIRED,
                priority=1,
            )
        ],
    )

    with Session(database_engine, expire_on_commit=False) as first_session:
        JobRepository(first_session).create(job)

    with Session(database_engine, expire_on_commit=False) as second_session:
        persisted = JobRepository(second_session).get(job_id)

        assert persisted is not None
        assert persisted.title == "Data Engineer"
        assert persisted.status == JobStatus.DRAFT
        assert [requirement.text for requirement in persisted.requirements] == ["Python"]


def test_repository_lists_newest_jobs_first(db_session: Session) -> None:
    repository = JobRepository(db_session)
    first_id = uuid4()
    second_id = uuid4()
    first = Job(
        id=first_id,
        code=f"JOB-{first_id.hex[:8].upper()}",
        title="First",
        location="Dhaka",
        employment_type=EmploymentType.FULL_TIME,
        application_email="first@example.com",
        status=JobStatus.DRAFT,
    )
    second = Job(
        id=second_id,
        code=f"JOB-{second_id.hex[:8].upper()}",
        title="Second",
        location="Dhaka",
        employment_type=EmploymentType.FULL_TIME,
        application_email="second@example.com",
        status=JobStatus.DRAFT,
    )

    repository.create(first)
    repository.create(second)

    assert {job.id for job in repository.list_all()} == {first_id, second_id}
