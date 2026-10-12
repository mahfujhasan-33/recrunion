from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScoringDimension(StrEnum):
    TECHNICAL_SKILLS = "TECHNICAL_SKILLS"
    RELEVANT_EXPERIENCE = "RELEVANT_EXPERIENCE"
    ACADEMIC_PROFESSIONAL_QUALIFICATIONS = "ACADEMIC_PROFESSIONAL_QUALIFICATIONS"
    ROLE_SPECIFIC_CRITERIA = "ROLE_SPECIFIC_CRITERIA"


class EvidenceCoverage(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class JobScoringConfig(Base):
    __tablename__ = "job_scoring_configs"
    __table_args__ = (
        CheckConstraint(
            "technical_skills_weight > 0 AND relevant_experience_weight > 0 "
            "AND qualifications_weight > 0 AND role_specific_criteria_weight > 0",
            name="ck_job_scoring_config_positive_weights",
        ),
        UniqueConstraint("job_id", name="uq_job_scoring_config_job"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    technical_skills_weight: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("1"), server_default="1"
    )
    relevant_experience_weight: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("1"), server_default="1"
    )
    qualifications_weight: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("1"), server_default="1"
    )
    role_specific_criteria_weight: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("1"), server_default="1"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CandidateScore(Base):
    __tablename__ = "candidate_scores"
    __table_args__ = (
        CheckConstraint(
            "overall_score >= 0 AND overall_score <= 100",
            name="ck_candidate_score_range",
        ),
        UniqueConstraint("application_id", name="uq_candidate_score_application"),
        Index("ix_candidate_scores_job_id", "job_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"), nullable=False
    )
    screening_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_screenings.id", ondelete="CASCADE"), nullable=False
    )
    overall_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    dimension_breakdown: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    evidence_coverage: Mapped[EvidenceCoverage] = mapped_column(
        Enum(
            EvidenceCoverage,
            name="candidate_score_evidence_coverage",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    scoring_config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scoring_config_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_requirement_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_screened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(20), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
