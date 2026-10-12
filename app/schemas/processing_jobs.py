from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.processing_jobs import ProcessingJobStatus, ProcessingJobType


class ProcessingJobResponse(BaseModel):
    id: UUID
    job_type: ProcessingJobType
    entity_type: str
    entity_id: UUID
    status: ProcessingJobStatus
    progress: int
    progress_message: str
    attempt_count: int
    max_attempts: int
    error_code: str | None
    error_message_safe: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
