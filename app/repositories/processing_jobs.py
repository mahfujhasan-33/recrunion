from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.processing_jobs import ProcessingJob, ProcessingJobStatus


class ProcessingJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, processing_job: ProcessingJob) -> ProcessingJob:
        self._session.add(processing_job)
        self._commit()
        return processing_job

    def get(self, task_id: UUID) -> ProcessingJob | None:
        return self._session.get(ProcessingJob, task_id)

    def delete_queued_for_entity(self, entity_type: str, entity_id: UUID) -> None:
        self._session.execute(
            delete(ProcessingJob).where(
                ProcessingJob.entity_type == entity_type,
                ProcessingJob.entity_id == entity_id,
                ProcessingJob.status == ProcessingJobStatus.QUEUED,
            )
        )
        self._commit()

    def claim_next(self) -> ProcessingJob | None:
        statement = (
            select(ProcessingJob)
            .where(ProcessingJob.status == ProcessingJobStatus.QUEUED)
            .order_by(ProcessingJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        task = self._session.scalar(statement)
        if task is None:
            self._session.rollback()
            return None
        now = datetime.now(UTC)
        task.status = ProcessingJobStatus.RUNNING
        task.attempt_count += 1
        task.started_at = task.started_at or now
        task.heartbeat_at = now
        self._commit()
        return task

    def complete(self, task: ProcessingJob) -> None:
        task.status = ProcessingJobStatus.COMPLETED
        task.progress = 100
        task.progress_message = "Completed"
        task.completed_at = datetime.now(UTC)
        task.heartbeat_at = task.completed_at
        task.error_code = None
        task.error_message_safe = None
        self._commit()

    def fail_or_retry(self, task: ProcessingJob, code: str, message: str) -> None:
        task.error_code = code
        task.error_message_safe = message
        task.heartbeat_at = datetime.now(UTC)
        if task.attempt_count < task.max_attempts:
            task.status = ProcessingJobStatus.QUEUED
            task.progress_message = "Retry queued"
        else:
            task.status = ProcessingJobStatus.FAILED
            task.progress_message = "Failed"
            task.completed_at = datetime.now(UTC)
        self._commit()

    def update_progress(self, task: ProcessingJob, progress: int, message: str) -> None:
        task.progress = progress
        task.progress_message = message
        task.heartbeat_at = datetime.now(UTC)
        self._commit()

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
