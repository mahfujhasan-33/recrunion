"""Add CV processing, candidate profiles, and candidate evidence vectors.

Revision ID: 20261008_0007
Revises: 20261008_0006
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20261008_0007"
down_revision: str | None = "20261008_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        "job_type IN ('COMPANY_DOCUMENT_INGESTION', 'ASSISTANT_TURN', "
        "'JOB_PUBLICATION', 'PROCESS_CANDIDATE_DOCUMENT')",
    )

    op.add_column(
        "candidate_documents",
        sa.Column(
            "processing_status",
            sa.Enum(
                "IMPORTED",
                "QUEUED",
                "PROCESSING",
                "READY",
                "NEEDS_REVIEW",
                "FAILED",
                name="candidate_document_processing_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'IMPORTED'"),
            nullable=False,
        ),
    )
    op.add_column(
        "candidate_documents",
        sa.Column("processing_task_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "candidate_documents",
        sa.Column("safe_error_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "candidate_documents",
        sa.Column("safe_error_message", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "candidate_documents",
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "candidate_documents",
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
    )
    op.add_column(
        "candidate_documents",
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "candidate_documents",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "candidate_documents",
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_candidate_document_processing_task",
        "candidate_documents",
        "processing_jobs",
        ["processing_task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_candidate_document_processing_task",
        "candidate_documents",
        ["processing_task_id"],
    )
    op.create_index(
        "ix_candidate_documents_processing_status",
        "candidate_documents",
        ["processing_status"],
    )

    op.create_table(
        "candidate_cv_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["document_id"], ["candidate_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_candidate_cv_chunks_document_id",
        "candidate_cv_chunks",
        ["document_id"],
    )
    op.create_index(
        "uq_candidate_cv_chunks_document_index",
        "candidate_cv_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )

    op.create_table(
        "candidate_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("structured_json", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["application_id"], ["job_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["candidate_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_document_id"),
    )
    op.create_index(
        "ix_candidate_profiles_candidate_id",
        "candidate_profiles",
        ["candidate_id"],
    )
    op.create_index(
        "ix_candidate_profiles_application_id",
        "candidate_profiles",
        ["application_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_profiles_application_id", table_name="candidate_profiles")
    op.drop_index("ix_candidate_profiles_candidate_id", table_name="candidate_profiles")
    op.drop_table("candidate_profiles")
    op.drop_index("uq_candidate_cv_chunks_document_index", table_name="candidate_cv_chunks")
    op.drop_index("ix_candidate_cv_chunks_document_id", table_name="candidate_cv_chunks")
    op.drop_table("candidate_cv_chunks")
    op.drop_index("ix_candidate_documents_processing_status", table_name="candidate_documents")
    op.drop_constraint(
        "uq_candidate_document_processing_task",
        "candidate_documents",
        type_="unique",
    )
    op.drop_constraint(
        "fk_candidate_document_processing_task",
        "candidate_documents",
        type_="foreignkey",
    )
    op.drop_column("candidate_documents", "processed_at")
    op.drop_column("candidate_documents", "processing_started_at")
    op.drop_column("candidate_documents", "chunk_count")
    op.drop_column("candidate_documents", "embedding_dimension")
    op.drop_column("candidate_documents", "embedding_model")
    op.drop_column("candidate_documents", "safe_error_message")
    op.drop_column("candidate_documents", "safe_error_code")
    op.drop_column("candidate_documents", "processing_task_id")
    op.drop_column("candidate_documents", "processing_status")

    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        "job_type IN ('COMPANY_DOCUMENT_INGESTION', 'ASSISTANT_TURN', 'JOB_PUBLICATION')",
    )
