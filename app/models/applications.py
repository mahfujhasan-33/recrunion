from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
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
        Index("ix_candidate_documents_job_id", "job_id"),
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    application: Mapped[JobApplication] = relationship(back_populates="document")
