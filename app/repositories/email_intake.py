from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models.applications import ApplicationIntakeReceipt, CandidateDocument
from app.models.jobs import Job
from app.models.processing_jobs import (
    ProcessingJob,
    ProcessingJobStatus,
    ProcessingJobType,
)


class EmailIntakeRepository:
    """Persist external receipts and email-sync task coordination state."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_message(self, external_message_id: str) -> ApplicationIntakeReceipt | None:
        return self._session.scalar(
            select(ApplicationIntakeReceipt).where(
                ApplicationIntakeReceipt.source_type == "EMAIL",
                ApplicationIntakeReceipt.external_message_id == external_message_id,
            )
        )

    def create_receipt(self, receipt: ApplicationIntakeReceipt) -> ApplicationIntakeReceipt:
        self._session.add(receipt)
        self._commit()
        return receipt

    def update_receipt(self, receipt: ApplicationIntakeReceipt) -> ApplicationIntakeReceipt:
        self._commit()
        return receipt

    def get_receipt(self, receipt_id: UUID) -> ApplicationIntakeReceipt | None:
        return self._session.get(ApplicationIntakeReceipt, receipt_id)

    def get_task(self, task_id: UUID) -> ProcessingJob | None:
        return self._session.get(ProcessingJob, task_id)

    def get_document(self, document_id: UUID) -> CandidateDocument | None:
        return self._session.get(CandidateDocument, document_id)

    def get_job(self, job_id: UUID) -> Job | None:
        return self._session.scalar(
            select(Job).where(Job.id == job_id).options(selectinload(Job.requirements))
        )

    def lock_job(self, job_id: UUID) -> Job | None:
        return self._session.scalar(
            select(Job)
            .where(Job.id == job_id)
            .options(selectinload(Job.requirements))
            .with_for_update()
        )

    def save_job(self, job: Job) -> None:
        self._commit()

    def list_receipts_for_task(self, task_id: UUID) -> list[ApplicationIntakeReceipt]:
        statement = (
            select(ApplicationIntakeReceipt)
            .where(ApplicationIntakeReceipt.processing_task_id == task_id)
            .order_by(ApplicationIntakeReceipt.created_at, ApplicationIntakeReceipt.id)
        )
        return list(self._session.scalars(statement))

    def latest_task_for_job(self, job_id: UUID) -> ProcessingJob | None:
        statement = (
            select(ProcessingJob)
            .where(
                ProcessingJob.job_type == ProcessingJobType.SYNC_EMAIL_APPLICATIONS,
                ProcessingJob.entity_type == "JOB",
                ProcessingJob.entity_id == job_id,
            )
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
        return self._session.scalar(statement)

    def active_task_for_job(self, job_id: UUID) -> ProcessingJob | None:
        statement = (
            select(ProcessingJob)
            .where(
                ProcessingJob.job_type == ProcessingJobType.SYNC_EMAIL_APPLICATIONS,
                ProcessingJob.entity_type == "JOB",
                ProcessingJob.entity_id == job_id,
                ProcessingJob.status.in_([ProcessingJobStatus.QUEUED, ProcessingJobStatus.RUNNING]),
            )
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
        return self._session.scalar(statement)

    def queue_task(self, task: ProcessingJob) -> ProcessingJob:
        self._session.add(task)
        self._commit()
        return task

    def rollback(self) -> None:
        """Restore a usable transaction after an isolated message failure."""

        self._session.rollback()

    def list_eligible_jobs(self) -> list[Job]:
        statement = (
            select(Job)
            .where(Job.approved_at.is_not(None))
            .options(selectinload(Job.requirements))
            .order_by(Job.created_at)
        )
        return list(self._session.scalars(statement))

    @staticmethod
    def task_is_due(
        task: ProcessingJob | None,
        *,
        now: datetime,
        interval_seconds: int,
    ) -> bool:
        if task is None:
            return True
        if task.status in {ProcessingJobStatus.QUEUED, ProcessingJobStatus.RUNNING}:
            return False
        return (now - task.created_at).total_seconds() >= interval_seconds

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
