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
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InterviewStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"


class InvitationStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class TranscriptParticipantRole(StrEnum):
    CANDIDATE = "CANDIDATE"
    INTERVIEWER = "INTERVIEWER"


class InterviewSession(Base):
    __tablename__ = "interview_sessions"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_interview_session_application"),
        UniqueConstraint(
            "candidate_access_token_hash",
            name="uq_interview_session_candidate_token_hash",
        ),
        Index("ix_interview_sessions_job_scheduled", "job_id", "scheduled_start_at"),
        Index("ix_interview_sessions_candidate_id", "candidate_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_applications.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[InterviewStatus] = mapped_column(
        Enum(
            InterviewStatus,
            name="interview_status",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
        default=InterviewStatus.SCHEDULED,
        server_default=InterviewStatus.SCHEDULED.value,
    )
    scheduled_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    display_timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    invitation_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    invitation_status: Mapped[InvitationStatus] = mapped_column(
        Enum(
            InvitationStatus,
            name="interview_invitation_status",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
        default=InvitationStatus.PENDING,
        server_default=InvitationStatus.PENDING.value,
    )
    invitation_provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invitation_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invitation_error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    invitation_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InterviewTranscriptSegment(Base):
    __tablename__ = "interview_transcript_segments"
    __table_args__ = (
        UniqueConstraint(
            "interview_id",
            "participant_identity",
            "segment_key",
            name="uq_transcript_segment_identity_key",
        ),
        Index(
            "ix_transcript_segments_interview_started",
            "interview_id",
            "started_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    interview_id: Mapped[UUID] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False
    )
    participant_role: Mapped[TranscriptParticipantRole] = mapped_column(
        Enum(
            TranscriptParticipantRole,
            name="transcript_participant_role",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    participant_identity: Mapped[str] = mapped_column(String(100), nullable=False)
    segment_key: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text: Mapped[str] = mapped_column(String(8000), nullable=False)
    language: Mapped[str | None] = mapped_column(String(35), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_segment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
