from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AssistantMessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class AssistantArtifactType(StrEnum):
    JOB_REQUIREMENTS = "JOB_REQUIREMENTS"
    JOB_DESCRIPTION = "JOB_DESCRIPTION"
    JOB_DESCRIPTION_PROPOSAL = "JOB_DESCRIPTION_PROPOSAL"


class AssistantArtifactStatus(StrEnum):
    WORKING = "WORKING"
    READY = "READY"
    PROPOSED = "PROPOSED"
    APPLIED = "APPLIED"
    FAILED = "FAILED"


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New hiring task")
    active_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    messages: Mapped[list["AssistantMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["AssistantArtifact"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        Index("ix_assistant_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[AssistantMessageRole] = mapped_column(
        Enum(
            AssistantMessageRole,
            name="assistant_message_role",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    conversation: Mapped[AssistantConversation] = relationship(back_populates="messages")


class AssistantArtifact(Base):
    __tablename__ = "assistant_artifacts"
    __table_args__ = (
        Index("ix_assistant_artifacts_conversation_type", "conversation_id", "artifact_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[AssistantArtifactType] = mapped_column(
        Enum(
            AssistantArtifactType,
            name="assistant_artifact_type",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
    )
    status: Mapped[AssistantArtifactStatus] = mapped_column(
        Enum(
            AssistantArtifactStatus,
            name="assistant_artifact_status",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
        default=AssistantArtifactStatus.WORKING,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    base_job_version: Mapped[int | None] = mapped_column(nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    validation: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    conversation: Mapped[AssistantConversation] = relationship(back_populates="artifacts")
