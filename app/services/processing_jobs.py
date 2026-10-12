from uuid import UUID

from app.errors import ProcessingTaskNotFoundError
from app.repositories.processing_jobs import ProcessingJobRepository
from app.schemas.processing_jobs import ProcessingJobResponse


class ProcessingJobService:
    def __init__(self, repository: ProcessingJobRepository) -> None:
        self._repository = repository

    def get_task(self, task_id: UUID) -> ProcessingJobResponse:
        task = self._repository.get(task_id)
        if task is None:
            raise ProcessingTaskNotFoundError
        return ProcessingJobResponse.model_validate(task, from_attributes=True)
