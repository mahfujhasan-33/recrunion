"""Create jobs and job requirements.

Revision ID: 20261005_0001
Revises:
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=False),
        sa.Column(
            "employment_type",
            sa.Enum(
                "FULL_TIME",
                "PART_TIME",
                "CONTRACT",
                "INTERNSHIP",
                "TEMPORARY",
                name="employment_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("application_email", sa.String(length=320), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "DRAFT",
                "GENERATED",
                "APPROVED",
                "PUBLISHING",
                "PUBLISHED",
                "PUBLISH_FAILED",
                "CLOSED",
                name="job_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_code", "jobs", ["code"], unique=True)

    op.create_table(
        "job_requirements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "SKILL",
                "EXPERIENCE",
                "QUALIFICATION",
                "SELECTION_CRITERION",
                name="requirement_category",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "requirement_type",
            sa.Enum(
                "REQUIRED",
                "PREFERRED",
                name="requirement_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("minimum_value", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("weight", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_requirements_job_id",
        "job_requirements",
        ["job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_requirements_job_id", table_name="job_requirements")
    op.drop_table("job_requirements")
    op.drop_index("ix_jobs_code", table_name="jobs")
    op.drop_table("jobs")
