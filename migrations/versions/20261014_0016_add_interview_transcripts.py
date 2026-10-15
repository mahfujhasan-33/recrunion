"""Add persisted final interview transcript segments.

Revision ID: 20261014_0016
Revises: 20261013_0015
Create Date: 2026-10-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0016"
down_revision: str | None = "20261013_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_transcript_segments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("interview_id", sa.Uuid(), nullable=False),
        sa.Column(
            "participant_role",
            sa.Enum(
                "CANDIDATE",
                "INTERVIEWER",
                name="transcript_participant_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("participant_identity", sa.String(length=100), nullable=False),
        sa.Column("segment_key", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.String(length=8000), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("provider_segment_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["interview_id"],
            ["interview_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interview_id",
            "participant_identity",
            "segment_key",
            name="uq_transcript_segment_identity_key",
        ),
    )
    op.create_index(
        "ix_transcript_segments_interview_started",
        "interview_transcript_segments",
        ["interview_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transcript_segments_interview_started",
        table_name="interview_transcript_segments",
    )
    op.drop_table("interview_transcript_segments")
