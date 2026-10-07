from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.adapters.publisher import PublicationResult
from app.models.job_publications import JobPublication, PublicationStatus
from app.models.jobs import Job, JobStatus
from app.models.processing_jobs import ProcessingJob


class JobPublicationRepository:
    """Persist publication attempts and their job lifecycle transitions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_job(self, job_id: UUID) -> Job | None:
        statement = (
            select(Job)
            .where(Job.id == job_id)
            .options(selectinload(Job.requirements))
            .with_for_update()
        )
        return self._session.scalar(statement)

    def get_job(self, job_id: UUID) -> Job | None:
        statement = select(Job).where(Job.id == job_id).options(selectinload(Job.requirements))
        return self._session.scalar(statement)

    def get(self, publication_id: UUID) -> JobPublication | None:
        statement = (
            select(JobPublication)
            .where(JobPublication.id == publication_id)
            .options(selectinload(JobPublication.job).selectinload(Job.requirements))
        )
        return self._session.scalar(statement)

    def latest_for_job(self, job_id: UUID) -> JobPublication | None:
        statement = (
            select(JobPublication)
            .where(JobPublication.job_id == job_id)
            .order_by(JobPublication.attempt_number.desc())
            .limit(1)
        )
        return self._session.scalar(statement)

    def next_attempt_number(self, job_id: UUID) -> int:
        statement = select(func.max(JobPublication.attempt_number)).where(
            JobPublication.job_id == job_id
        )
        return int(self._session.scalar(statement) or 0) + 1

    def create_attempt(
        self,
        job: Job,
        publication: JobPublication,
        task: ProcessingJob,
    ) -> JobPublication:
        job.status = JobStatus.PUBLISHING
        self._session.add_all([publication, task])
        self._commit()
        return publication

    def mark_publishing(self, publication: JobPublication) -> None:
        publication.status = PublicationStatus.PUBLISHING
        self._commit()

    def mark_published(
        self,
        publication: JobPublication,
        result: PublicationResult,
    ) -> JobPublication:
        publication.status = PublicationStatus.PUBLISHED
        publication.external_post_uri = result.external_post_uri
        publication.external_record_id = result.external_record_id
        publication.external_url = result.external_url
        publication.published_at = result.published_at
        publication.error_code = None
        publication.error_message_safe = None
        publication.job.status = JobStatus.PUBLISHED
        self._commit()
        return publication

    def mark_failed(
        self,
        publication: JobPublication,
        *,
        code: str,
        message: str,
    ) -> JobPublication:
        publication.status = PublicationStatus.FAILED
        publication.error_code = code
        publication.error_message_safe = message
        publication.job.status = JobStatus.PUBLISH_FAILED
        self._commit()
        return publication

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
