from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from app.models.applications import ApplicationSource, ApplicationStatus


class ApplicationResponse(BaseModel):
    id: UUID
    reference: str
    candidate_id: UUID
    candidate_display_reference: str
    original_filename: str
    file_size: int
    source: ApplicationSource
    status: ApplicationStatus
    imported_at: datetime


class ApplicationUploadOutcome(StrEnum):
    IMPORTED = "IMPORTED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    INVALID_FILE = "INVALID_FILE"
    FAILED = "FAILED"


class ApplicationUploadFileResult(BaseModel):
    filename: str
    outcome: ApplicationUploadOutcome
    message: str
    application: ApplicationResponse | None = None


class ApplicationBatchUploadResponse(BaseModel):
    received: int
    imported: int
    duplicates: int
    invalid: int
    failed: int
    files: list[ApplicationUploadFileResult]
