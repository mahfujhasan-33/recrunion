from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.dependencies import get_application_intake_service, get_candidate_processing_service
from app.schemas.applications import (
    ApplicationBatchUploadResponse,
    ApplicationDetailResponse,
    ApplicationResponse,
    CandidateProcessingBatchResponse,
    CandidateProcessingStartResponse,
)
from app.services.application_intake import ApplicationIntakeService, IncomingCandidateDocument
from app.services.candidate_processing import CandidateProcessingService

router = APIRouter(prefix="/api/v1/jobs/{job_id}/applications", tags=["applications"])
ApplicationIntakeServiceDependency = Annotated[
    ApplicationIntakeService,
    Depends(get_application_intake_service),
]
CandidateProcessingServiceDependency = Annotated[
    CandidateProcessingService,
    Depends(get_candidate_processing_service),
]


@router.post("/upload", response_model=ApplicationBatchUploadResponse)
def upload_applications(
    job_id: UUID,
    service: ApplicationIntakeServiceDependency,
    files: Annotated[list[UploadFile], File()],
) -> ApplicationBatchUploadResponse:
    uploads = [
        IncomingCandidateDocument(
            filename=file.filename or "",
            content_type=file.content_type or "",
            source=file.file,
        )
        for file in files
    ]
    return service.upload_batch(job_id, uploads)


@router.get("", response_model=list[ApplicationResponse])
def list_applications(
    job_id: UUID,
    service: ApplicationIntakeServiceDependency,
) -> list[ApplicationResponse]:
    return service.list_applications(job_id)


@router.post(
    "/process-pending",
    response_model=CandidateProcessingBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def process_pending_applications(
    job_id: UUID,
    service: CandidateProcessingServiceDependency,
) -> CandidateProcessingBatchResponse:
    return service.queue_pending(job_id)


@router.post(
    "/{application_id}/process",
    response_model=CandidateProcessingStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def process_application(
    job_id: UUID,
    application_id: UUID,
    service: CandidateProcessingServiceDependency,
) -> CandidateProcessingStartResponse:
    return service.queue(job_id, application_id)


@router.get("/{application_id}", response_model=ApplicationDetailResponse)
def get_application(
    job_id: UUID,
    application_id: UUID,
    service: ApplicationIntakeServiceDependency,
) -> ApplicationDetailResponse:
    return service.get_application(job_id, application_id)
