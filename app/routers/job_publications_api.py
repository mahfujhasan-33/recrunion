from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.dependencies import get_job_publishing_service
from app.schemas.job_publications import JobPublicationResponse
from app.services.job_publications import JobPublishingService

router = APIRouter(prefix="/api/v1/jobs", tags=["job publications"])
JobPublishingServiceDependency = Annotated[
    JobPublishingService, Depends(get_job_publishing_service)
]


@router.post(
    "/{job_id}/publish",
    response_model=JobPublicationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def publish_job(
    job_id: UUID,
    service: JobPublishingServiceDependency,
) -> JobPublicationResponse:
    return service.publish(job_id)


@router.post(
    "/{job_id}/publication/retry",
    response_model=JobPublicationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_job_publication(
    job_id: UUID,
    service: JobPublishingServiceDependency,
) -> JobPublicationResponse:
    return service.retry(job_id)


@router.get(
    "/{job_id}/publication",
    response_model=JobPublicationResponse | None,
)
def get_job_publication(
    job_id: UUID,
    service: JobPublishingServiceDependency,
) -> JobPublicationResponse | None:
    return service.get_latest(job_id)
