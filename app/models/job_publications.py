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
from app.models.jobs import Job


class PublicationStatus(StrEnum):
    QUEUED = "QUEUED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class JobPublication(Base):
    __tablename__ = "job_publications"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number", name="uq_job_publication_attempt"),
        Index("ix_job_publications_job_created", "job_id", "created_at"),
        Index("ix_job_publications_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    processing_task_id: Mapped[UUID] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(
        Enum(
            PublicationStatus,
            name="publication_status",
            native_enum=False,
            create_constraint=True,
        ),
        nullable=False,
        default=PublicationStatus.QUEUED,
        server_default=PublicationStatus.QUEUED.value,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    external_post_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    external_record_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message_safe: Mapped[str | None] = mapped_column(String(500), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    job: Mapped[Job] = relationship()
