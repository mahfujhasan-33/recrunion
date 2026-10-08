from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.applications import (
    ApplicationSource,
    ApplicationStatus,
    CandidateDocumentProcessingStatus,
)
from app.schemas.candidate_profiles import CandidateProfileData


class ApplicationResponse(BaseModel):
    id: UUID
    reference: str
    candidate_id: UUID
    candidate_display_reference: str
    original_filename: str
    file_size: int
    source: ApplicationSource
    status: ApplicationStatus
    processing_status: CandidateDocumentProcessingStatus
    processing_task_id: UUID | None
    processing_error_code: str | None
    processing_error_message: str | None
    imported_at: datetime


class CandidateEvidenceResponse(BaseModel):
    id: UUID
    page_number: int
    content: str


class CandidateProfileResponse(BaseModel):
    profile: CandidateProfileData
    provider: str
    model: str
    evidence: list[CandidateEvidenceResponse]
    created_at: datetime


class ApplicationDetailResponse(ApplicationResponse):
    candidate_profile: CandidateProfileResponse | None = None


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


class CandidateProcessingStartResponse(BaseModel):
    application_id: UUID
    document_id: UUID
    processing_status: CandidateDocumentProcessingStatus
    task_id: UUID


class CandidateProcessingBatchResponse(BaseModel):
    queued: int = Field(ge=0)
    tasks: list[CandidateProcessingStartResponse]
