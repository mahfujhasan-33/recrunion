from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, UploadFile

from app.dependencies import get_application_intake_service
from app.schemas.applications import ApplicationBatchUploadResponse, ApplicationResponse
from app.services.application_intake import ApplicationIntakeService, IncomingCandidateDocument

router = APIRouter(prefix="/api/v1/jobs/{job_id}/applications", tags=["applications"])
ApplicationIntakeServiceDependency = Annotated[
    ApplicationIntakeService,
    Depends(get_application_intake_service),
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


@router.get("/{application_id}", response_model=ApplicationResponse)
def get_application(
    job_id: UUID,
    application_id: UUID,
    service: ApplicationIntakeServiceDependency,
) -> ApplicationResponse:
    return service.get_application(job_id, application_id)
