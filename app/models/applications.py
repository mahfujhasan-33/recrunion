from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ApplicationSource(StrEnum):
    MANUAL_UPLOAD = "MANUAL_UPLOAD"


class ApplicationStatus(StrEnum):
    IMPORTED = "IMPORTED"


class CandidateDocumentProcessingStatus(StrEnum):
    IMPORTED = "IMPORTED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    display_reference: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    applications: Mapped[list["JobApplication"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
    )


class JobApplication(Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        Index("ix_job_applications_job_created", "job_id", "created_at"),
        Index("ix_job_applications_candidate_id", "candidate_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[ApplicationSource] = mapped_column(
        Enum(
            ApplicationSource,
            name="application_source",
            native_enum=False,
            create_constraint=False,
            length=50,
        ),
        nullable=False,
        default=ApplicationSource.MANUAL_UPLOAD,
        server_default=ApplicationSource.MANUAL_UPLOAD.value,
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(
            ApplicationStatus,
            name="application_status",
            native_enum=False,
            create_constraint=False,
            length=50,
        ),
        nullable=False,
        default=ApplicationStatus.IMPORTED,
        server_default=ApplicationStatus.IMPORTED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    candidate: Mapped[Candidate] = relationship(back_populates="applications")
    document: Mapped["CandidateDocument"] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        uselist=False,
    )


class CandidateDocument(Base):
    __tablename__ = "candidate_documents"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_candidate_document_application"),
        UniqueConstraint("job_id", "sha256", name="uq_candidate_document_job_sha256"),
        UniqueConstraint(
            "processing_task_id",
            name="uq_candidate_document_processing_task",
        ),
        Index("ix_candidate_documents_job_id", "job_id"),
        Index("ix_candidate_documents_processing_status", "processing_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_status: Mapped[CandidateDocumentProcessingStatus] = mapped_column(
        Enum(
            CandidateDocumentProcessingStatus,
            name="candidate_document_processing_status",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
        default=CandidateDocumentProcessingStatus.IMPORTED,
        server_default=CandidateDocumentProcessingStatus.IMPORTED.value,
    )
    processing_task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    safe_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    application: Mapped[JobApplication] = relationship(back_populates="document")
    chunks: Mapped[list["CandidateCVChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="CandidateCVChunk.chunk_index",
    )
    profile: Mapped["CandidateProfile | None"] = relationship(
        back_populates="source_document",
        cascade="all, delete-orphan",
        uselist=False,
    )


class CandidateCVChunk(Base):
    __tablename__ = "candidate_cv_chunks"
    __table_args__ = (
        Index("ix_candidate_cv_chunks_document_id", "document_id"),
        Index(
            "uq_candidate_cv_chunks_document_index",
            "document_id",
            "chunk_index",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    document: Mapped[CandidateDocument] = relationship(back_populates="chunks")


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    __table_args__ = (
        Index("ix_candidate_profiles_candidate_id", "candidate_id"),
        Index("ix_candidate_profiles_application_id", "application_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_documents.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    structured_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    source_document: Mapped[CandidateDocument] = relationship(back_populates="profile")
