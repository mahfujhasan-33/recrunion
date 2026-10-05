from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db_session
from app.repositories.jobs import JobRepository
from app.services.jobs import JobService


def get_job_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> JobService:
    """Provide a job service using the request-scoped database session."""

    return JobService(JobRepository(session))
