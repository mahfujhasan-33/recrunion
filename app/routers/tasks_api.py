from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.dependencies import get_processing_job_service
from app.schemas.processing_jobs import ProcessingJobResponse
from app.services.processing_jobs import ProcessingJobService

router = APIRouter(prefix="/api/v1/tasks", tags=["processing tasks"])
ProcessingJobServiceDependency = Annotated[
    ProcessingJobService, Depends(get_processing_job_service)
]


@router.get("/{task_id}", response_model=ProcessingJobResponse)
def get_processing_task(
    task_id: UUID,
    service: ProcessingJobServiceDependency,
) -> ProcessingJobResponse:
    return service.get_task(task_id)
