"""Add approved-job publication attempts and worker task type.

Revision ID: 20261007_0005
Revises: 20261007_0004
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_0005"
down_revision: str | None = "20261007_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        "job_type IN ('COMPANY_DOCUMENT_INGESTION', 'ASSISTANT_TURN', 'JOB_PUBLICATION')",
    )
    op.create_table(
        "job_publications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("processing_task_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "PUBLISHING",
                "PUBLISHED",
                "FAILED",
                name="publication_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'QUEUED'"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("external_post_uri", sa.String(length=500), nullable=True),
        sa.Column("external_record_id", sa.String(length=200), nullable=True),
        sa.Column("external_url", sa.String(length=500), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message_safe", sa.String(length=500), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["processing_task_id"], ["processing_jobs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_publication_attempt"),
        sa.UniqueConstraint("processing_task_id"),
    )
    op.create_index(
        "ix_job_publications_job_created",
        "job_publications",
        ["job_id", "created_at"],
    )
    op.create_index("ix_job_publications_status", "job_publications", ["status"])


def downgrade() -> None:
    op.drop_index("ix_job_publications_status", table_name="job_publications")
    op.drop_index("ix_job_publications_job_created", table_name="job_publications")
    op.drop_table("job_publications")
    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        "job_type IN ('COMPANY_DOCUMENT_INGESTION', 'ASSISTANT_TURN')",
    )
