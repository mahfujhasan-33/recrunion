"""Add generated and reviewed job description fields.

Revision ID: 20261005_0002
Revises: 20261005_0001
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261005_0002"
down_revision: str | None = "20261005_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("jd_generated_content", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("jd_content", sa.Text(), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("jd_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("jd_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("jobs", sa.Column("jd_provider", sa.String(length=50), nullable=True))
    op.add_column("jobs", sa.Column("jd_model", sa.String(length=100), nullable=True))
    op.add_column("jobs", sa.Column("jd_generation_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "jd_generation_metadata")
    op.drop_column("jobs", "jd_model")
    op.drop_column("jobs", "jd_provider")
    op.drop_column("jobs", "approved_at")
    op.drop_column("jobs", "jd_generated_at")
    op.drop_column("jobs", "jd_version")
    op.drop_column("jobs", "jd_content")
    op.drop_column("jobs", "jd_generated_content")
