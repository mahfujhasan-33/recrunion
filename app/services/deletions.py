from uuid import UUID

from app.adapters.document_storage import DocumentStorage
from app.errors import (
    ApplicationNotFoundError,
    DeletionInProgressError,
    JobNotFoundError,
)
from app.repositories.deletions import DeletionRepository


class DeletionService:
    """Delete recruitment aggregates and their managed local documents."""

    def __init__(
        self,
        repository: DeletionRepository,
        document_storage: DocumentStorage,
    ) -> None:
        self._repository = repository
        self._document_storage = document_storage

    def delete_application(self, job_id: UUID, application_id: UUID) -> None:
        plan = self._repository.plan_application_deletion(job_id, application_id)
        if plan is None:
            raise ApplicationNotFoundError
        if plan.is_busy:
            raise DeletionInProgressError(
                "Wait for application processing or email synchronization to finish before "
                "deleting this application."
            )
        self._document_storage.delete(plan.storage_key)
        self._repository.delete_application(plan)

    def delete_job(self, job_id: UUID) -> None:
        plan = self._repository.plan_job_deletion(job_id)
        if plan is None:
            raise JobNotFoundError(job_id)
        if plan.is_busy:
            raise DeletionInProgressError(
                "Wait for this job's active assistant, publication, intake, processing, or "
                "screening task to finish before deleting it."
            )
        for storage_key in plan.storage_keys:
            self._document_storage.delete(storage_key)
        self._repository.delete_job(plan)
