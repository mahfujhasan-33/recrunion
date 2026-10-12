"""Add recruiter batch CV application intake.

Revision ID: 20261008_0006
Revises: 20261007_0005
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_0006"
down_revision: str | None = "20261007_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("display_reference", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("display_reference"),
    )
    op.create_table(
        "job_applications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=50),
            server_default="MANUAL_UPLOAD",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            server_default="IMPORTED",
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_applications_candidate_id",
        "job_applications",
        ["candidate_id"],
    )
    op.create_index(
        "ix_job_applications_job_created",
        "job_applications",
        ["job_id", "created_at"],
    )
    op.create_table(
        "candidate_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["application_id"], ["job_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", name="uq_candidate_document_application"),
        sa.UniqueConstraint("job_id", "sha256", name="uq_candidate_document_job_sha256"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_candidate_documents_job_id",
        "candidate_documents",
        ["job_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_documents_job_id", table_name="candidate_documents")
    op.drop_table("candidate_documents")
    op.drop_index("ix_job_applications_job_created", table_name="job_applications")
    op.drop_index("ix_job_applications_candidate_id", table_name="job_applications")
    op.drop_table("job_applications")
    op.drop_table("candidates")
