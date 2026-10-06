from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.dependencies import get_job_description_service, get_job_service
from app.schemas.job_descriptions import JobDescriptionUpdateRequest
from app.schemas.jobs import JobResponse, JobWriteRequest
from app.services.job_descriptions import JobDescriptionService
from app.services.jobs import JobService

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
JobServiceDependency = Annotated[JobService, Depends(get_job_service)]
JobDescriptionServiceDependency = Annotated[
    JobDescriptionService,
    Depends(get_job_description_service),
]


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


@router.post("/{job_id}/generate-description", response_model=JobResponse)
async def generate_job_description(
    job_id: UUID,
    service: JobDescriptionServiceDependency,
) -> JobResponse:
    return await service.generate_description(job_id)


@router.put("/{job_id}/description", response_model=JobResponse)
def update_job_description(
    job_id: UUID,
    request: JobDescriptionUpdateRequest,
    service: JobDescriptionServiceDependency,
) -> JobResponse:
    return service.update_description(job_id, request)


@router.post("/{job_id}/approve", response_model=JobResponse)
def approve_job_description(
    job_id: UUID,
    service: JobDescriptionServiceDependency,
) -> JobResponse:
    return service.approve_description(job_id)
