from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.dependencies import get_interview_scheduling_service
from app.schemas.interviews import (
    InterviewResponse,
    InterviewScheduleRequest,
    InterviewSchedulingDefaults,
)
from app.services.interview_scheduling import InterviewSchedulingService

router = APIRouter(tags=["interviews"])
InterviewSchedulingServiceDependency = Annotated[
    InterviewSchedulingService,
    Depends(get_interview_scheduling_service),
]


@router.post(
    "/api/v1/applications/{application_id}/interviews",
    response_model=InterviewResponse,
    status_code=status.HTTP_201_CREATED,
)
async def schedule_interview(
    application_id: UUID,
    payload: InterviewScheduleRequest,
    service: InterviewSchedulingServiceDependency,
) -> InterviewResponse:
    return await service.schedule(application_id, payload)


@router.get(
    "/api/v1/applications/{application_id}/interview-scheduling-defaults",
    response_model=InterviewSchedulingDefaults,
)
def get_interview_scheduling_defaults(
    application_id: UUID,
    service: InterviewSchedulingServiceDependency,
) -> InterviewSchedulingDefaults:
    return service.get_scheduling_defaults(application_id)


@router.get("/api/v1/interviews/{interview_id}", response_model=InterviewResponse)
def get_interview(
    interview_id: UUID,
    service: InterviewSchedulingServiceDependency,
) -> InterviewResponse:
    return service.get_interview(interview_id)


@router.get("/api/v1/jobs/{job_id}/interviews", response_model=list[InterviewResponse])
def list_job_interviews(
    job_id: UUID,
    service: InterviewSchedulingServiceDependency,
) -> list[InterviewResponse]:
    return service.list_for_job(job_id)


@router.post(
    "/api/v1/interviews/{interview_id}/resend-invitation",
    response_model=InterviewResponse,
)
async def resend_interview_invitation(
    interview_id: UUID,
    service: InterviewSchedulingServiceDependency,
) -> InterviewResponse:
    return await service.resend_invitation(interview_id)
