from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.dependencies import get_email_intake_service
from app.schemas.email_intake import EmailSyncQueuedResponse, EmailSyncStatusResponse
from app.services.email_intake import EmailApplicationIntakeService

router = APIRouter(prefix="/api/v1/jobs/{job_id}/applications", tags=["email intake"])
EmailIntakeServiceDependency = Annotated[
    EmailApplicationIntakeService,
    Depends(get_email_intake_service),
]


@router.post(
    "/email-sync",
    response_model=EmailSyncQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def sync_email_applications(
    job_id: UUID,
    service: EmailIntakeServiceDependency,
) -> EmailSyncQueuedResponse:
    return service.queue(job_id)


@router.get("/email-sync", response_model=EmailSyncStatusResponse)
def get_email_sync_status(
    job_id: UUID,
    service: EmailIntakeServiceDependency,
) -> EmailSyncStatusResponse:
    return service.status(job_id)
