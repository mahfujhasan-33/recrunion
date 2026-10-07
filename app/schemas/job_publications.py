from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.job_publications import PublicationStatus


class JobPublicationResponse(BaseModel):
    id: UUID
    job_id: UUID
    processing_task_id: UUID
    provider: str
    status: PublicationStatus
    attempt_number: int
    external_post_uri: str | None
    external_record_id: str | None
    external_url: str | None
    error_code: str | None
    error_message_safe: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicationConfirmationRequest(BaseModel):
    confirmed: bool
