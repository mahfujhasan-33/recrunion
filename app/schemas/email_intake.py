from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.applications import DriveArchiveStatus, IntakeReceiptStatus
from app.models.processing_jobs import ProcessingJobStatus


class EmailIntakeReceiptResponse(BaseModel):
    id: UUID
    status: IntakeReceiptStatus
    original_filename: str | None
    archive_status: DriveArchiveStatus
    candidate_document_id: UUID | None
    drive_file_url: str | None
    safe_error_code: str | None
    safe_error_message: str | None
    created_at: datetime


class EmailSyncSummary(BaseModel):
    matched: int = Field(ge=0)
    imported: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    needs_review: int = Field(ge=0)
    rejected: int = Field(ge=0)
    drive_archived: int = Field(ge=0)
    drive_archive_failed: int = Field(ge=0)


class EmailSyncQueuedResponse(BaseModel):
    task_id: UUID
    status: ProcessingJobStatus


class EmailSyncStatusResponse(BaseModel):
    task_id: UUID | None
    task_status: ProcessingJobStatus | None
    progress: int | None
    progress_message: str | None
    last_sync_at: datetime | None
    summary: EmailSyncSummary | None
    receipts: list[EmailIntakeReceiptResponse]
