"""Add the recruiter assistant workspace and task progress messages.

Revision ID: 20261007_0004
Revises: 20261006_0003
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_0004"
down_revision: str | None = "20261006_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        "job_type IN ('COMPANY_DOCUMENT_INGESTION', 'ASSISTANT_TURN')",
    )
    op.add_column(
        "processing_jobs",
        sa.Column(
            "progress_message",
            sa.String(length=200),
            server_default="Queued",
            nullable=False,
        ),
    )

    op.create_table(
        "assistant_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("active_job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["active_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "USER",
                "ASSISTANT",
                "SYSTEM",
                name="assistant_message_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["assistant_conversations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_messages_conversation_created",
        "assistant_messages",
        ["conversation_id", "created_at"],
    )
    op.create_table(
        "assistant_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "artifact_type",
            sa.Enum(
                "JOB_REQUIREMENTS",
                "JOB_DESCRIPTION",
                "JOB_DESCRIPTION_PROPOSAL",
                name="assistant_artifact_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "WORKING",
                "READY",
                "PROPOSED",
                "APPLIED",
                "FAILED",
                name="assistant_artifact_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("base_job_version", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["assistant_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_artifacts_conversation_type",
        "assistant_artifacts",
        ["conversation_id", "artifact_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_artifacts_conversation_type", table_name="assistant_artifacts")
    op.drop_table("assistant_artifacts")
    op.drop_index("ix_assistant_messages_conversation_created", table_name="assistant_messages")
    op.drop_table("assistant_messages")
    op.drop_table("assistant_conversations")
    op.drop_column("processing_jobs", "progress_message")
    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        "job_type IN ('COMPANY_DOCUMENT_INGESTION')",
    )
