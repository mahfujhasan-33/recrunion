"""Widen processing job type for candidate screening tasks.

Revision ID: 20261008_0009
Revises: 20261008_0008
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_0009"
down_revision: str | None = "20261008_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "processing_jobs",
        "job_type",
        existing_type=sa.String(length=26),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "processing_jobs",
        "job_type",
        existing_type=sa.String(length=64),
        type_=sa.String(length=28),
        existing_nullable=False,
    )
