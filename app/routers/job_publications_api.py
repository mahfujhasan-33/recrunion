from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.dependencies import get_job_publishing_service
from app.schemas.job_publications import (
    JobPublicationBatchResponse,
    JobPublicationRequest,
    JobPublicationResponse,
    JobPublicationRetryRequest,
    PublicationProvider,
)
from app.services.job_publications import JobPublishingService

router = APIRouter(prefix="/api/v1/jobs", tags=["job publications"])
JobPublishingServiceDependency = Annotated[
    JobPublishingService, Depends(get_job_publishing_service)
]


@router.post(
    "/{job_id}/publish",
    response_model=JobPublicationBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def publish_job(
    job_id: UUID,
    service: JobPublishingServiceDependency,
    request: JobPublicationRequest | None = None,
) -> JobPublicationBatchResponse:
    return service.publish(job_id, request.providers if request else None)


@router.post(
    "/{job_id}/publication/retry",
    response_model=JobPublicationBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_job_publication(
    job_id: UUID,
    service: JobPublishingServiceDependency,
    request: JobPublicationRetryRequest | None = None,
) -> JobPublicationBatchResponse:
    return service.retry(job_id, request.provider if request else None)


@router.post(
    "/{job_id}/publication/{provider}/retry",
    response_model=JobPublicationBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_provider_publication(
    job_id: UUID,
    provider: PublicationProvider,
    service: JobPublishingServiceDependency,
) -> JobPublicationBatchResponse:
    return service.retry(job_id, provider)


@router.get(
    "/{job_id}/publication",
    response_model=JobPublicationResponse | None,
)
def get_job_publication(
    job_id: UUID,
    service: JobPublishingServiceDependency,
) -> JobPublicationResponse | None:
    return service.get_latest(job_id)


@router.get(
    "/{job_id}/publications",
    response_model=list[JobPublicationResponse],
)
def list_job_publications(
    job_id: UUID,
    service: JobPublishingServiceDependency,
) -> list[JobPublicationResponse]:
    return service.list_latest(job_id)
