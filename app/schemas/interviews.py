from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.adapters.video import VideoParticipantRole
from app.models.interviews import InterviewStatus, InvitationStatus


class InvitationEmailSource(StrEnum):
    APPLICATION_EMAIL = "APPLICATION_EMAIL"
    CV_EXTRACTED_EMAIL = "CV_EXTRACTED_EMAIL"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class InterviewScheduleRequest(BaseModel):
    invitation_email: EmailStr
    scheduled_start_local: datetime
    timezone: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(ge=15, le=240)


class InterviewSchedulingDefaults(BaseModel):
    application_id: UUID
    candidate_reference: str
    invitation_email: str | None
    email_source: InvitationEmailSource


class InterviewResponse(BaseModel):
    id: UUID
    job_id: UUID
    application_id: UUID
    candidate_id: UUID
    candidate_reference: str
    job_title: str
    status: InterviewStatus
    scheduled_start_at: datetime
    duration_minutes: int
    timezone: str
    invitation_status: InvitationStatus
    invitation_email: str | None
    invitation_sent_at: datetime | None
    invitation_error_code: str | None
    invitation_error_message: str | None
    candidate_join_url: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CandidateInterviewView(BaseModel):
    job_title: str
    status: InterviewStatus
    scheduled_start_at: datetime
    duration_minutes: int
    timezone: str


class InterviewRoomView(BaseModel):
    interview_id: UUID
    job_title: str
    candidate_reference: str | None
    role: VideoParticipantRole
    status: InterviewStatus
    scheduled_start_at: datetime
    duration_minutes: int
    timezone: str
    livekit_url: str | None = None
    participant_token: str | None = None
    participant_identity: str | None = None
    provider_configured: bool
    provider_error_message: str | None = None
    lifecycle_sync_url: str
    end_url: str | None = None
    join_allowed: bool = True
    started_at: datetime | None = None
    ended_at: datetime | None = None


class InterviewLifecycleResponse(BaseModel):
    interview_id: UUID
    status: InterviewStatus
    started_at: datetime | None
    ended_at: datetime | None
