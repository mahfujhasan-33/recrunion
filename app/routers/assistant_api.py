from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.dependencies import get_assistant_service
from app.schemas.assistant import (
    AssistantApprovalRequest,
    AssistantArtifactUpdate,
    AssistantConversationResponse,
    AssistantMessageCreate,
    AssistantTurnQueuedResponse,
)
from app.schemas.job_publications import JobPublicationResponse, PublicationConfirmationRequest
from app.services.assistant import AssistantService

router = APIRouter(prefix="/api/v1/assistant", tags=["recruiter assistant"])
AssistantServiceDependency = Annotated[AssistantService, Depends(get_assistant_service)]


@router.post(
    "/conversations",
    response_model=AssistantConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(service: AssistantServiceDependency) -> AssistantConversationResponse:
    return service.create_conversation()


@router.get("/conversations/latest", response_model=AssistantConversationResponse)
def get_latest_conversation(service: AssistantServiceDependency) -> AssistantConversationResponse:
    return service.get_latest_or_create()


@router.get("/conversations/{conversation_id}", response_model=AssistantConversationResponse)
def get_conversation(
    conversation_id: UUID, service: AssistantServiceDependency
) -> AssistantConversationResponse:
    return service.get_conversation(conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=AssistantTurnQueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_message(
    conversation_id: UUID,
    request: AssistantMessageCreate,
    service: AssistantServiceDependency,
) -> AssistantTurnQueuedResponse:
    return service.queue_turn(conversation_id, request.content)


@router.put(
    "/conversations/{conversation_id}/artifacts/{artifact_id}",
    response_model=AssistantConversationResponse,
)
def update_requirements(
    conversation_id: UUID,
    artifact_id: UUID,
    request: AssistantArtifactUpdate,
    service: AssistantServiceDependency,
) -> AssistantConversationResponse:
    return service.update_requirements(conversation_id, artifact_id, request.payload)


@router.post(
    "/conversations/{conversation_id}/proposals/{artifact_id}/apply",
    response_model=AssistantConversationResponse,
)
def apply_proposal(
    conversation_id: UUID,
    artifact_id: UUID,
    service: AssistantServiceDependency,
) -> AssistantConversationResponse:
    return service.apply_proposal(conversation_id, artifact_id)


@router.post(
    "/conversations/{conversation_id}/approve",
    response_model=AssistantConversationResponse,
)
def confirm_approval(
    conversation_id: UUID,
    request: AssistantApprovalRequest,
    service: AssistantServiceDependency,
) -> AssistantConversationResponse:
    return service.confirm_approval(conversation_id, request.confirmed)


@router.post(
    "/conversations/{conversation_id}/publish",
    response_model=JobPublicationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def confirm_publication(
    conversation_id: UUID,
    request: PublicationConfirmationRequest,
    service: AssistantServiceDependency,
) -> JobPublicationResponse:
    return service.confirm_publication(
        conversation_id,
        request.confirmed,
        retry=False,
    )


@router.post(
    "/conversations/{conversation_id}/publication/retry",
    response_model=JobPublicationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def confirm_publication_retry(
    conversation_id: UUID,
    request: PublicationConfirmationRequest,
    service: AssistantServiceDependency,
) -> JobPublicationResponse:
    return service.confirm_publication(
        conversation_id,
        request.confirmed,
        retry=True,
    )
