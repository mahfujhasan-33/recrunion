"""Widen Gmail attachment identifiers.

Revision ID: 20261012_0012
Revises: 20261012_0011
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0012"
down_revision: str | None = "20261012_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "application_intake_receipts",
        "external_attachment_id",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "application_intake_receipts",
        "external_attachment_id",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
