"""Expand the persisted interview call lifecycle.

Revision ID: 20261013_0015
Revises: 20261013_0014
Create Date: 2026-10-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261013_0015"
down_revision: str | None = "20261013_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("interview_status", "interview_sessions", type_="check")
    op.create_check_constraint(
        "interview_status",
        "interview_sessions",
        "status IN ('SCHEDULED', 'WAITING', 'ACTIVE', 'ENDED')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE interview_sessions SET status = 'SCHEDULED' "
        "WHERE status IN ('WAITING', 'ACTIVE', 'ENDED')"
    )
    op.drop_constraint("interview_status", "interview_sessions", type_="check")
    op.create_check_constraint(
        "interview_status",
        "interview_sessions",
        "status IN ('SCHEDULED')",
    )
