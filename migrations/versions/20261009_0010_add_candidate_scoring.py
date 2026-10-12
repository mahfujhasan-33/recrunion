"""Add deterministic F2 candidate suitability scoring.

Revision ID: 20261009_0010
Revises: 20261008_0009
Create Date: 2026-10-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261009_0010"
down_revision: str | None = "20261008_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_scoring_configs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "technical_skills_weight",
            sa.Numeric(precision=10, scale=4),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "relevant_experience_weight",
            sa.Numeric(precision=10, scale=4),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "qualifications_weight",
            sa.Numeric(precision=10, scale=4),
            server_default="1",
            nullable=False,
        ),
        sa.Column(
            "role_specific_criteria_weight",
            sa.Numeric(precision=10, scale=4),
            server_default="1",
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
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
        sa.CheckConstraint(
            "technical_skills_weight > 0 AND relevant_experience_weight > 0 "
            "AND qualifications_weight > 0 AND role_specific_criteria_weight > 0",
            name="ck_job_scoring_config_positive_weights",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_job_scoring_config_job"),
    )

    op.create_table(
        "candidate_scores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("screening_id", sa.Uuid(), nullable=False),
        sa.Column("overall_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("dimension_breakdown", sa.JSON(), nullable=False),
        sa.Column(
            "evidence_coverage",
            sa.Enum(
                "HIGH",
                "MEDIUM",
                "LOW",
                name="candidate_score_evidence_coverage",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("scoring_config_version", sa.Integer(), nullable=False),
        sa.Column("scoring_config_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_requirement_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_screened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("algorithm_version", sa.String(length=20), nullable=False),
        sa.Column(
            "calculated_at",
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
        sa.CheckConstraint(
            "overall_score >= 0 AND overall_score <= 100",
            name="ck_candidate_score_range",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["application_id"], ["job_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["screening_id"], ["candidate_screenings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", name="uq_candidate_score_application"),
    )
    op.create_index("ix_candidate_scores_job_id", "candidate_scores", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_candidate_scores_job_id", table_name="candidate_scores")
    op.drop_table("candidate_scores")
    op.drop_table("job_scoring_configs")
