from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.company_documents import CompanyDocumentStatus, CompanyDocumentType


class CompanyDocumentResponse(BaseModel):
    id: UUID
    original_filename: str
    document_type: CompanyDocumentType
    content_type: str
    size_bytes: int
    sha256: str
    status: CompanyDocumentStatus
    safe_error_code: str | None
    safe_error_message: str | None
    embedding_model: str | None
    embedding_dimension: int | None
    chunk_count: int
    uploaded_at: datetime
    processing_started_at: datetime | None
    processed_at: datetime | None
    updated_at: datetime


class CompanyDocumentUploadResponse(BaseModel):
    document: CompanyDocumentResponse
    task_id: UUID
