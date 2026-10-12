from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CompanyDocumentType(StrEnum):
    HIRING_POLICY = "HIRING_POLICY"
    RECRUITMENT_POLICY = "RECRUITMENT_POLICY"
    HR_GUIDELINE = "HR_GUIDELINE"
    JOB_DESCRIPTION_STANDARD = "JOB_DESCRIPTION_STANDARD"
    QUALIFICATION_RULE = "QUALIFICATION_RULE"
    OTHER = "OTHER"


class CompanyDocumentStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"
    DELETE_FAILED = "DELETE_FAILED"


class CompanyDocument(Base):
    __tablename__ = "company_documents"
    __table_args__ = (Index("ix_company_documents_status", "status"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    document_type: Mapped[CompanyDocumentType] = mapped_column(
        Enum(
            CompanyDocumentType,
            name="company_document_type",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    status: Mapped[CompanyDocumentStatus] = mapped_column(
        Enum(
            CompanyDocumentStatus,
            name="company_document_status",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
        default=CompanyDocumentStatus.UPLOADED,
        server_default=CompanyDocumentStatus.UPLOADED.value,
    )
    safe_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    chunks: Mapped[list["CompanyDocumentChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="CompanyDocumentChunk.chunk_index",
    )


class CompanyDocumentChunk(Base):
    __tablename__ = "company_document_chunks"
    __table_args__ = (
        Index("ix_company_document_chunks_document_id", "document_id"),
        Index(
            "uq_company_document_chunks_document_index",
            "document_id",
            "chunk_index",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("company_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    document: Mapped[CompanyDocument] = relationship(back_populates="chunks")
