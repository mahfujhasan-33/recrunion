from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.dependencies import get_candidate_screening_service
from app.schemas.screening import (
    CandidateScreeningResponse,
    JobScreeningResponse,
    ScreeningBatchResponse,
    ScreeningStartResponse,
)
from app.services.candidate_screening import CandidateScreeningService

router = APIRouter(prefix="/api/v1/jobs/{job_id}", tags=["candidate screening"])
CandidateScreeningServiceDependency = Annotated[
    CandidateScreeningService,
    Depends(get_candidate_screening_service),
]


@router.post(
    "/applications/screen-ready",
    response_model=ScreeningBatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def screen_ready_applications(
    job_id: UUID,
    service: CandidateScreeningServiceDependency,
) -> ScreeningBatchResponse:
    return service.queue_ready(job_id)


@router.post(
    "/applications/{application_id}/screen",
    response_model=ScreeningStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def screen_application(
    job_id: UUID,
    application_id: UUID,
    service: CandidateScreeningServiceDependency,
) -> ScreeningStartResponse:
    return service.queue(job_id, application_id)


@router.get("/screening", response_model=JobScreeningResponse)
def get_job_screening(
    job_id: UUID,
    service: CandidateScreeningServiceDependency,
) -> JobScreeningResponse:
    return service.get_job_screening(job_id)


@router.get(
    "/applications/{application_id}/screening",
    response_model=CandidateScreeningResponse,
)
def get_candidate_screening(
    job_id: UUID,
    application_id: UUID,
    service: CandidateScreeningServiceDependency,
) -> CandidateScreeningResponse:
    return service.get_screening(job_id, application_id)
