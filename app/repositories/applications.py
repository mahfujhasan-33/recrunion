from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models.applications import Candidate, CandidateDocument, JobApplication


class ApplicationRepository:
    """Persist candidate intake records as one cohesive aggregate."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_document_by_job_and_sha256(
        self,
        job_id: UUID,
        sha256: str,
    ) -> CandidateDocument | None:
        return self._session.scalar(
            select(CandidateDocument).where(
                CandidateDocument.job_id == job_id,
                CandidateDocument.sha256 == sha256,
            )
        )

    def create(
        self,
        candidate: Candidate,
        application: JobApplication,
        document: CandidateDocument,
    ) -> JobApplication:
        self._session.add_all([candidate, application, document])
        self._commit()
        return self.get_for_job(application.job_id, application.id) or application

    def list_for_job(self, job_id: UUID) -> list[JobApplication]:
        statement = (
            select(JobApplication)
            .where(JobApplication.job_id == job_id)
            .options(
                selectinload(JobApplication.candidate),
                selectinload(JobApplication.document),
            )
            .order_by(JobApplication.created_at.desc(), JobApplication.id.desc())
        )
        return list(self._session.scalars(statement))

    def get_for_job(self, job_id: UUID, application_id: UUID) -> JobApplication | None:
        statement = (
            select(JobApplication)
            .where(
                JobApplication.job_id == job_id,
                JobApplication.id == application_id,
            )
            .options(
                selectinload(JobApplication.candidate),
                selectinload(JobApplication.document),
            )
        )
        return self._session.scalar(statement)

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
