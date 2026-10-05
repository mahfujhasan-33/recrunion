from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.dependencies import get_job_service
from app.schemas.jobs import JobResponse, JobWriteRequest
from app.services.jobs import JobService

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
JobServiceDependency = Annotated[JobService, Depends(get_job_service)]


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(request: JobWriteRequest, service: JobServiceDependency) -> JobResponse:
    return service.create_job(request)


@router.get("", response_model=list[JobResponse])
def list_jobs(service: JobServiceDependency) -> list[JobResponse]:
    return service.list_jobs()


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: UUID, service: JobServiceDependency) -> JobResponse:
    return service.get_job(job_id)


@router.put("/{job_id}", response_model=JobResponse)
def update_job(
    job_id: UUID,
    request: JobWriteRequest,
    service: JobServiceDependency,
) -> JobResponse:
    return service.update_job(job_id, request)
