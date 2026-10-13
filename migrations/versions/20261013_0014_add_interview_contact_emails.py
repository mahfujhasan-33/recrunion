"""Persist application contact and confirmed interview invitation emails.

Revision ID: 20261013_0014
Revises: 20261012_0013
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0014"
down_revision: str | None = "20261012_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "job_applications",
        sa.Column("contact_email", sa.String(length=320), nullable=True),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("invitation_email", sa.String(length=320), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "invitation_email")
    op.drop_column("job_applications", "contact_email")
