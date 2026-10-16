from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProbeStatus(StrEnum):
    READY = "READY"
    ASKED = "ASKED"
    DISMISSED = "DISMISSED"


class AssistanceKind(StrEnum):
    PROBES = "PROBES"
    FOLLOW_UP = "FOLLOW_UP"


class InterviewAssistanceRun(Base):
    __tablename__ = "interview_assistance_runs"
    __table_args__ = (UniqueConstraint("interview_id", "request_id", name="uq_assistance_request"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    interview_id: Mapped[UUID] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), unique=True
    )
    kind: Mapped[AssistanceKind] = mapped_column(
        Enum(AssistanceKind, native_enum=False, create_constraint=True)
    )
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    request_text: Mapped[str | None] = mapped_column(String(1500))
    response: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InterviewProbe(Base):
    __tablename__ = "interview_probes"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("interview_assistance_runs.id", ondelete="CASCADE"), index=True
    )
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"), index=True
    )
    requirement_id: Mapped[UUID] = mapped_column(Uuid)
    requirement_text: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    expected_evidence: Mapped[list[str]] = mapped_column(JSON)
    evidence_refs: Mapped[dict] = mapped_column(JSON)
    priority: Mapped[int] = mapped_column(Integer)
    status: Mapped[ProbeStatus] = mapped_column(
        Enum(ProbeStatus, native_enum=False, create_constraint=True), default=ProbeStatus.READY
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
