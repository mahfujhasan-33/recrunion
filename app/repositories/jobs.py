from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models.jobs import Job


class JobRepository:
    """Persist and retrieve jobs with their requirement records."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, job: Job) -> Job:
        self._session.add(job)
        self._commit()
        return self.get(job.id) or job

    def list_all(self) -> list[Job]:
        statement = (
            select(Job)
            .options(selectinload(Job.requirements))
            .order_by(Job.created_at.desc(), Job.code.desc())
        )
        return list(self._session.scalars(statement).unique())

    def get(self, job_id: UUID) -> Job | None:
        statement = select(Job).where(Job.id == job_id).options(selectinload(Job.requirements))
        return self._session.scalar(statement)

    def update(self, job: Job) -> Job:
        self._commit()
        return self.get(job.id) or job

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
