"""Add the persistent company-policy knowledge base.

Revision ID: 20261006_0003
Revises: 20261005_0002
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20261006_0003"
down_revision: str | None = "20261005_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "company_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column(
            "document_type",
            sa.Enum(
                "HIRING_POLICY",
                "RECRUITMENT_POLICY",
                "HR_GUIDELINE",
                "JOB_DESCRIPTION_STANDARD",
                "QUALIFICATION_RULE",
                "OTHER",
                name="company_document_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "UPLOADED",
                "PROCESSING",
                "READY",
                "FAILED",
                "DELETING",
                "DELETE_FAILED",
                name="company_document_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'UPLOADED'"),
            nullable=False,
        ),
        sa.Column("safe_error_code", sa.String(length=100), nullable=True),
        sa.Column("safe_error_message", sa.String(length=500), nullable=True),
        sa.Column("embedding_model", sa.String(length=200), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_company_documents_sha256", "company_documents", ["sha256"], unique=True)
    op.create_index("ix_company_documents_status", "company_documents", ["status"], unique=False)

    op.create_table(
        "company_document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_title", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["document_id"], ["company_documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_document_chunks_document_id",
        "company_document_chunks",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "uq_company_document_chunks_document_index",
        "company_document_chunks",
        ["document_id", "chunk_index"],
        unique=True,
    )

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "job_type",
            sa.Enum(
                "COMPANY_DOCUMENT_INGESTION",
                name="processing_job_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                name="processing_job_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'QUEUED'"),
            nullable=False,
        ),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message_safe", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_processing_jobs_status_created",
        "processing_jobs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_processing_jobs_entity",
        "processing_jobs",
        ["entity_type", "entity_id"],
        unique=False,
    )

    op.create_table(
        "job_description_policy_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("jd_version", sa.Integer(), nullable=False),
        sa.Column("jd_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("retrieval_count", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_policy_reviews_job_version",
        "job_description_policy_reviews",
        ["job_id", "jd_version", "created_at"],
        unique=False,
    )

    op.create_table(
        "job_description_policy_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_id", sa.Uuid(), nullable=False),
        sa.Column("source_chunk_id", sa.Uuid(), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("source_document_type", sa.String(length=100), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("evidence_excerpt", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_title", sa.String(length=500), nullable=True),
        sa.Column("retrieval_similarity", sa.Float(), nullable=False),
        sa.Column(
            "related_jd_section",
            sa.Enum(
                "SUMMARY",
                "RESPONSIBILITIES",
                "REQUIRED_SKILLS",
                "PREFERRED_SKILLS",
                "QUALIFICATIONS",
                "EXPERIENCE",
                "APPLICATION",
                "GENERAL",
                name="job_description_section",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "MET",
                "PARTIALLY_MET",
                "NOT_MET",
                "NOT_APPLICABLE",
                name="policy_alignment_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["review_id"], ["job_description_policy_reviews.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_policy_findings_review_id",
        "job_description_policy_findings",
        ["review_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_policy_findings_review_id", table_name="job_description_policy_findings")
    op.drop_table("job_description_policy_findings")
    op.drop_index("ix_job_policy_reviews_job_version", table_name="job_description_policy_reviews")
    op.drop_table("job_description_policy_reviews")
    op.drop_index("ix_processing_jobs_entity", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_status_created", table_name="processing_jobs")
    op.drop_table("processing_jobs")
    op.drop_index("uq_company_document_chunks_document_index", table_name="company_document_chunks")
    op.drop_index("ix_company_document_chunks_document_id", table_name="company_document_chunks")
    op.drop_table("company_document_chunks")
    op.drop_index("ix_company_documents_status", table_name="company_documents")
    op.drop_index("ix_company_documents_sha256", table_name="company_documents")
    op.drop_table("company_documents")
