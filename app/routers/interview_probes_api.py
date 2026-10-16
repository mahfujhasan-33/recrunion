from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.dependencies import get_interview_probe_service
from app.schemas.interview_probes import (
    AssistanceRunResponse,
    CopilotRequest,
    GenerateProbesRequest,
    InterviewAssistanceResponse,
    ProbeResponse,
    ProbeStatusUpdate,
)
from app.services.interview_probes import InterviewProbeService

router = APIRouter(prefix="/api/v1/interviews/{interview_id}", tags=["interview assistance"])
Service = Annotated[InterviewProbeService, Depends(get_interview_probe_service)]


@router.get("/probes", response_model=InterviewAssistanceResponse)
def get_probes(interview_id: UUID, service: Service) -> InterviewAssistanceResponse:
    return service.get(interview_id)


@router.post("/probes/generate", response_model=AssistanceRunResponse, status_code=202)
def generate_probes(
    interview_id: UUID, payload: GenerateProbesRequest, service: Service
) -> AssistanceRunResponse:
    return service.queue(interview_id, payload.request_id, regenerate=payload.regenerate)


@router.patch("/probes/{probe_id}", response_model=ProbeResponse)
def update_probe(
    interview_id: UUID, probe_id: UUID, payload: ProbeStatusUpdate, service: Service
) -> ProbeResponse:
    return service.update_status(interview_id, probe_id, payload.status)


@router.post("/copilot", response_model=AssistanceRunResponse, status_code=202)
def ask_copilot(
    interview_id: UUID, payload: CopilotRequest, service: Service
) -> AssistanceRunResponse:
    return service.queue(interview_id, payload.request_id, message=payload.message)
