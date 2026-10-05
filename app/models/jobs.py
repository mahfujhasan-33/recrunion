from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EmploymentType(StrEnum):
    FULL_TIME = "FULL_TIME"
    PART_TIME = "PART_TIME"
    CONTRACT = "CONTRACT"
    INTERNSHIP = "INTERNSHIP"
    TEMPORARY = "TEMPORARY"


class JobStatus(StrEnum):
    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    APPROVED = "APPROVED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    CLOSED = "CLOSED"


class RequirementCategory(StrEnum):
    SKILL = "SKILL"
    EXPERIENCE = "EXPERIENCE"
    QUALIFICATION = "QUALIFICATION"
    SELECTION_CRITERION = "SELECTION_CRITERION"


class RequirementType(StrEnum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    employment_type: Mapped[EmploymentType] = mapped_column(
        Enum(EmploymentType, name="employment_type", native_enum=False, create_constraint=True),
        nullable=False,
    )
    application_email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", native_enum=False, create_constraint=True),
        nullable=False,
        default=JobStatus.DRAFT,
        server_default=JobStatus.DRAFT.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    requirements: Mapped[list["JobRequirement"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="JobRequirement.priority",
    )


class JobRequirement(Base):
    __tablename__ = "job_requirements"
    __table_args__ = (Index("ix_job_requirements_job_id", "job_id"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[RequirementCategory] = mapped_column(
        Enum(
            RequirementCategory,
            name="requirement_category",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_type: Mapped[RequirementType] = mapped_column(
        Enum(
            RequirementType,
            name="requirement_type",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_value: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    job: Mapped[Job] = relationship(back_populates="requirements")
