"""Add F11 assistance runs and persisted recruiter probes.

Revision ID: 20261016_0018
Revises: 20261016_0017
"""

import sqlalchemy as sa
from alembic import op

revision = "20261016_0018"
down_revision = "20261016_0017"
branch_labels = None
depends_on = None

TASK_TYPES = (
    "'COMPANY_DOCUMENT_INGESTION', 'ASSISTANT_TURN', 'JOB_PUBLICATION', "
    "'PROCESS_CANDIDATE_DOCUMENT', 'SCREEN_CANDIDATE_APPLICATION', 'SYNC_EMAIL_APPLICATIONS'"
)


def upgrade() -> None:
    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        f"job_type IN ({TASK_TYPES}, 'INTERVIEW_ASSISTANCE')",
    )
    op.create_table(
        "interview_assistance_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "interview_id",
            sa.Uuid(),
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column(
            "task_id",
            sa.Uuid(),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "PROBES",
                "FOLLOW_UP",
                name="assistancekind",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_text", sa.String(1500), nullable=True),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("interview_id", "request_id", name="uq_assistance_request"),
    )
    op.create_index(
        "ix_interview_assistance_runs_interview_id", "interview_assistance_runs", ["interview_id"]
    )
    op.create_table(
        "interview_probes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("interview_assistance_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "application_id",
            sa.Uuid(),
            sa.ForeignKey("job_applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requirement_id", sa.Uuid(), nullable=False),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expected_evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "READY",
                "ASKED",
                "DISMISSED",
                name="probestatus",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_interview_probes_run_id", "interview_probes", ["run_id"])
    op.create_index("ix_interview_probes_application_id", "interview_probes", ["application_id"])


def downgrade() -> None:
    op.drop_table("interview_probes")
    op.drop_table("interview_assistance_runs")
    op.execute("DELETE FROM processing_jobs WHERE job_type = 'INTERVIEW_ASSISTANCE'")
    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type", "processing_jobs", f"job_type IN ({TASK_TYPES})"
    )
