from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.applications import CandidateCVChunk
from app.models.jobs import RequirementCategory, RequirementType


class CandidateScreeningStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RequirementMatchStatus(StrEnum):
    MET = "MET"
    PARTIALLY_MET = "PARTIALLY_MET"
    UNMET = "UNMET"


class CandidateScreening(Base):
    __tablename__ = "candidate_screenings"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_candidate_screening_application"),
        UniqueConstraint("processing_task_id", name="uq_candidate_screening_task"),
        Index("ix_candidate_screenings_job_status", "job_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"), nullable=False
    )
    candidate_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_documents.id", ondelete="CASCADE"), nullable=False
    )
    processing_task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[CandidateScreeningStatus] = mapped_column(
        Enum(
            CandidateScreeningStatus,
            name="candidate_screening_status",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
        default=CandidateScreeningStatus.QUEUED,
        server_default=CandidateScreeningStatus.QUEUED.value,
    )
    requirements_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    required_met: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_partially_met: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    required_unmet: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preferred_met: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preferred_partially_met: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preferred_unmet: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safe_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    screened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    matches: Mapped[list["CandidateRequirementMatch"]] = relationship(
        back_populates="screening",
        cascade="all, delete-orphan",
        order_by="CandidateRequirementMatch.priority",
    )


class CandidateRequirementMatch(Base):
    __tablename__ = "candidate_requirement_matches"
    __table_args__ = (
        UniqueConstraint(
            "screening_id",
            "job_requirement_id",
            name="uq_candidate_match_screening_requirement",
        ),
        Index("ix_candidate_requirement_matches_screening_id", "screening_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    screening_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_screenings.id", ondelete="CASCADE"), nullable=False
    )
    job_requirement_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    category: Mapped[RequirementCategory] = mapped_column(
        Enum(
            RequirementCategory,
            name="screening_requirement_category",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    requirement_type: Mapped[RequirementType] = mapped_column(
        Enum(
            RequirementType,
            name="screening_requirement_type",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    minimum_value: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    match_status: Mapped[RequirementMatchStatus] = mapped_column(
        Enum(
            RequirementMatchStatus,
            name="candidate_requirement_match_status",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    screening: Mapped[CandidateScreening] = relationship(back_populates="matches")
    evidence: Mapped[list["CandidateRequirementEvidence"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
    )


class CandidateRequirementEvidence(Base):
    __tablename__ = "candidate_requirement_evidence"

    match_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_requirement_matches.id", ondelete="CASCADE"), primary_key=True
    )
    candidate_cv_chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_cv_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    retrieval_similarity: Mapped[float] = mapped_column(Float, nullable=False)
    match: Mapped[CandidateRequirementMatch] = relationship(back_populates="evidence")
    chunk: Mapped[CandidateCVChunk] = relationship()
