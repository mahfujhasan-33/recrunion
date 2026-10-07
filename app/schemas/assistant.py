from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.models.assistant import (
    AssistantArtifactStatus,
    AssistantArtifactType,
    AssistantMessageRole,
)
from app.models.jobs import EmploymentType
from app.schemas.job_publications import JobPublicationResponse
from app.schemas.jobs import JobResponse
from app.schemas.policy_findings import PolicyReviewResponse

ChatText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)]


class AssistantIntent(StrEnum):
    DRAFT_REQUIREMENTS = "DRAFT_REQUIREMENTS"
    UPDATE_REQUIREMENTS = "UPDATE_REQUIREMENTS"
    GENERATE_DESCRIPTION = "GENERATE_DESCRIPTION"
    ENHANCE_DESCRIPTION = "ENHANCE_DESCRIPTION"
    RECHECK_POLICY = "RECHECK_POLICY"
    APPROVE_DESCRIPTION = "APPROVE_DESCRIPTION"
    PUBLISH_JOB = "PUBLISH_JOB"
    RETRY_JOB_PUBLICATION = "RETRY_JOB_PUBLICATION"
    GET_PUBLICATION_STATUS = "GET_PUBLICATION_STATUS"
    HELP = "HELP"


class RequirementDraft(BaseModel):
    title: str | None = None
    location: str | None = None
    employment_type: EmploymentType | None = None
    application_email: str | None = None
    required_skills: list[str] = Field(default_factory=list, max_length=50)
    preferred_skills: list[str] = Field(default_factory=list, max_length=50)
    minimum_experience: float | None = Field(default=None, ge=0, le=80)
    qualifications: list[str] = Field(default_factory=list, max_length=50)
    selection_criteria: list[str] = Field(default_factory=list, max_length=50)


class AssistantTurnRequest(BaseModel):
    conversation_id: UUID
    user_message: ChatText
    current_requirements: RequirementDraft | None = None
    active_job: JobResponse | None = None


class AssistantTurnPlan(BaseModel):
    intent: AssistantIntent
    response_message: ChatText
    requirements: RequirementDraft | None = None


class AssistantMessageCreate(BaseModel):
    content: ChatText


class AssistantMessageResponse(BaseModel):
    id: UUID
    role: AssistantMessageRole
    content: str
    created_at: datetime


class AssistantArtifactUpdate(BaseModel):
    payload: dict[str, Any]


class AssistantArtifactResponse(BaseModel):
    id: UUID
    artifact_type: AssistantArtifactType
    status: AssistantArtifactStatus
    job_id: UUID | None
    base_job_version: int | None
    payload: dict[str, Any]
    validation: dict[str, Any]
    updated_at: datetime


class AssistantRequiredAction(BaseModel):
    code: str
    label: str
    severity: str
    message: str


class AssistantWorkspaceResponse(BaseModel):
    requirements: AssistantArtifactResponse | None
    description: AssistantArtifactResponse | None
    proposal: AssistantArtifactResponse | None
    job: JobResponse | None
    policy_review: PolicyReviewResponse | None
    publication: JobPublicationResponse | None
    required_actions: list[AssistantRequiredAction]


class AssistantConversationResponse(BaseModel):
    id: UUID
    title: str
    active_job_id: UUID | None
    created_at: datetime
    updated_at: datetime
    messages: list[AssistantMessageResponse]
    workspace: AssistantWorkspaceResponse


class AssistantTurnQueuedResponse(BaseModel):
    task_id: UUID
    conversation_id: UUID


class AssistantApprovalRequest(BaseModel):
    confirmed: bool
