from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PolicyAlignmentStatus(StrEnum):
    MET = "MET"
    PARTIALLY_MET = "PARTIALLY_MET"
    NOT_MET = "NOT_MET"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class JobDescriptionSection(StrEnum):
    SUMMARY = "SUMMARY"
    RESPONSIBILITIES = "RESPONSIBILITIES"
    REQUIRED_SKILLS = "REQUIRED_SKILLS"
    PREFERRED_SKILLS = "PREFERRED_SKILLS"
    QUALIFICATIONS = "QUALIFICATIONS"
    EXPERIENCE = "EXPERIENCE"
    APPLICATION = "APPLICATION"
    GENERAL = "GENERAL"


class JobDescriptionPolicyReview(Base):
    __tablename__ = "job_description_policy_reviews"
    __table_args__ = (
        Index("ix_job_policy_reviews_job_version", "job_id", "jd_version", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    jd_version: Mapped[int] = mapped_column(Integer, nullable=False)
    jd_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    findings: Mapped[list["JobDescriptionPolicyFinding"]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        order_by="JobDescriptionPolicyFinding.created_at",
    )


class JobDescriptionPolicyFinding(Base):
    __tablename__ = "job_description_policy_findings"
    __table_args__ = (Index("ix_job_policy_findings_review_id", "review_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_description_policy_reviews.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_chunk_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_document_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retrieval_similarity: Mapped[float] = mapped_column(Float, nullable=False)
    related_jd_section: Mapped[JobDescriptionSection] = mapped_column(
        Enum(
            JobDescriptionSection,
            name="job_description_section",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    status: Mapped[PolicyAlignmentStatus] = mapped_column(
        Enum(
            PolicyAlignmentStatus,
            name="policy_alignment_status",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    review: Mapped[JobDescriptionPolicyReview] = relationship(back_populates="findings")
