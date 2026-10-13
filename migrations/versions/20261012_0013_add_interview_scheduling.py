"""Add human-selected interview scheduling and invitation state.

Revision ID: 20261012_0013
Revises: 20261012_0012
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0013"
down_revision: str | None = "20261012_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "SCHEDULED",
                name="interview_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="SCHEDULED",
            nullable=False,
        ),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("display_timezone", sa.String(length=100), nullable=False),
        sa.Column("candidate_access_token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "invitation_status",
            sa.Enum(
                "PENDING",
                "SENT",
                "FAILED",
                "NOT_CONFIGURED",
                name="interview_invitation_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("invitation_provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("invitation_error_code", sa.String(length=100), nullable=True),
        sa.Column("invitation_error_message", sa.String(length=500), nullable=True),
        sa.Column("invitation_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["job_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", name="uq_interview_session_application"),
        sa.UniqueConstraint(
            "candidate_access_token_hash",
            name="uq_interview_session_candidate_token_hash",
        ),
    )
    op.create_index(
        "ix_interview_sessions_job_scheduled",
        "interview_sessions",
        ["job_id", "scheduled_start_at"],
    )
    op.create_index(
        "ix_interview_sessions_candidate_id",
        "interview_sessions",
        ["candidate_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_interview_sessions_candidate_id", table_name="interview_sessions")
    op.drop_index("ix_interview_sessions_job_scheduled", table_name="interview_sessions")
    op.drop_table("interview_sessions")
