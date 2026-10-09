"""Add evidence-backed F1 candidate screening and ranking inputs.

Revision ID: 20261008_0008
Revises: 20261008_0007
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_0008"
down_revision: str | None = "20261008_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        "job_type IN ('COMPANY_DOCUMENT_INGESTION', 'ASSISTANT_TURN', "
        "'JOB_PUBLICATION', 'PROCESS_CANDIDATE_DOCUMENT', "
        "'SCREEN_CANDIDATE_APPLICATION')",
    )

    op.create_table(
        "candidate_screenings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_document_id", sa.Uuid(), nullable=False),
        sa.Column("processing_task_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "QUEUED",
                "PROCESSING",
                "COMPLETED",
                "FAILED",
                name="candidate_screening_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="QUEUED",
            nullable=False,
        ),
        sa.Column("requirements_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("required_met", sa.Integer(), server_default="0", nullable=False),
        sa.Column("required_partially_met", sa.Integer(), server_default="0", nullable=False),
        sa.Column("required_unmet", sa.Integer(), server_default="0", nullable=False),
        sa.Column("preferred_met", sa.Integer(), server_default="0", nullable=False),
        sa.Column("preferred_partially_met", sa.Integer(), server_default="0", nullable=False),
        sa.Column("preferred_unmet", sa.Integer(), server_default="0", nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("safe_error_code", sa.String(length=100), nullable=True),
        sa.Column("safe_error_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("screened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["application_id"], ["job_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["candidate_document_id"], ["candidate_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["processing_task_id"], ["processing_jobs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", name="uq_candidate_screening_application"),
        sa.UniqueConstraint("processing_task_id", name="uq_candidate_screening_task"),
    )
    op.create_index(
        "ix_candidate_screenings_job_status",
        "candidate_screenings",
        ["job_id", "status"],
    )

    op.create_table(
        "candidate_requirement_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("screening_id", sa.Uuid(), nullable=False),
        sa.Column("job_requirement_id", sa.Uuid(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "SKILL",
                "EXPERIENCE",
                "QUALIFICATION",
                "SELECTION_CRITERION",
                name="screening_requirement_category",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "requirement_type",
            sa.Enum(
                "REQUIRED",
                "PREFERRED",
                name="screening_requirement_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("minimum_value", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "match_status",
            sa.Enum(
                "MET",
                "PARTIALLY_MET",
                "UNMET",
                name="candidate_requirement_match_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["screening_id"], ["candidate_screenings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "screening_id",
            "job_requirement_id",
            name="uq_candidate_match_screening_requirement",
        ),
    )
    op.create_index(
        "ix_candidate_requirement_matches_screening_id",
        "candidate_requirement_matches",
        ["screening_id"],
    )

    op.create_table(
        "candidate_requirement_evidence",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_cv_chunk_id", sa.Uuid(), nullable=False),
        sa.Column("retrieval_similarity", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["match_id"], ["candidate_requirement_matches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_cv_chunk_id"], ["candidate_cv_chunks.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("match_id", "candidate_cv_chunk_id"),
    )


def downgrade() -> None:
    op.drop_table("candidate_requirement_evidence")
    op.drop_index(
        "ix_candidate_requirement_matches_screening_id",
        table_name="candidate_requirement_matches",
    )
    op.drop_table("candidate_requirement_matches")
    op.drop_index("ix_candidate_screenings_job_status", table_name="candidate_screenings")
    op.drop_table("candidate_screenings")

    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        "job_type IN ('COMPANY_DOCUMENT_INGESTION', 'ASSISTANT_TURN', "
        "'JOB_PUBLICATION', 'PROCESS_CANDIDATE_DOCUMENT')",
    )
