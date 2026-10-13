from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

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
    candidate_join_url: str
    created_at: datetime
    updated_at: datetime


class CandidateInterviewView(BaseModel):
    job_title: str
    status: InterviewStatus
    scheduled_start_at: datetime
    duration_minutes: int
    timezone: str
