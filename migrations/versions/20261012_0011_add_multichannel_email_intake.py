"""Add multi-channel publishing and automated email application intake.

Revision ID: 20261012_0011
Revises: 20261009_0010
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0011"
down_revision: str | None = "20261009_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("application_code", sa.String(20), nullable=True))
    op.execute(
        "UPDATE jobs SET application_code = 'RU-' || "
        "upper(substr(replace(id::text, '-', ''), 1, 8))"
    )
    op.alter_column("jobs", "application_code", nullable=False)
    op.create_index("ix_jobs_application_code", "jobs", ["application_code"], unique=True)
    op.add_column("jobs", sa.Column("drive_folder_id", sa.String(200), nullable=True))
    op.add_column("jobs", sa.Column("drive_folder_url", sa.String(500), nullable=True))
    op.add_column(
        "jobs", sa.Column("drive_folder_created_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.drop_constraint("uq_job_publication_attempt", "job_publications", type_="unique")
    op.drop_constraint(
        "job_publications_processing_task_id_key", "job_publications", type_="unique"
    )
    op.add_column(
        "job_publications",
        sa.Column("published_jd_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute(
        "UPDATE job_publications AS publication SET published_jd_version = jobs.jd_version "
        "FROM jobs WHERE publication.job_id = jobs.id"
    )
    op.create_unique_constraint(
        "uq_job_publication_provider_attempt",
        "job_publications",
        ["job_id", "provider", "attempt_number"],
    )

    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        "job_type IN ('COMPANY_DOCUMENT_INGESTION', 'ASSISTANT_TURN', "
        "'JOB_PUBLICATION', 'PROCESS_CANDIDATE_DOCUMENT', "
        "'SCREEN_CANDIDATE_APPLICATION', 'SYNC_EMAIL_APPLICATIONS')",
    )

    op.create_table(
        "application_intake_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("processing_task_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("external_message_id", sa.String(255), nullable=False),
        sa.Column("external_attachment_id", sa.String(255), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "RECEIVED",
                "IMPORTED",
                "SKIPPED_DUPLICATE",
                "NO_CV_ATTACHMENT",
                "MULTIPLE_PDF_ATTACHMENTS",
                "UNSUPPORTED_FILE_TYPE",
                "INVALID_PDF",
                "OVERSIZED_FILE",
                "ATTACHMENT_FETCH_FAILED",
                "FAILED",
                name="intake_receipt_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="RECEIVED",
            nullable=False,
        ),
        sa.Column("candidate_document_id", sa.Uuid(), nullable=True),
        sa.Column("drive_file_id", sa.String(255), nullable=True),
        sa.Column("drive_file_url", sa.String(500), nullable=True),
        sa.Column(
            "archive_status",
            sa.Enum(
                "NOT_REQUIRED",
                "PENDING",
                "ARCHIVED",
                "RETRY_PENDING",
                name="drive_archive_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="NOT_REQUIRED",
            nullable=False,
        ),
        sa.Column("safe_error_code", sa.String(100), nullable=True),
        sa.Column("safe_error_message", sa.String(500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["processing_task_id"], ["processing_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["candidate_document_id"], ["candidate_documents.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type", "external_message_id", name="uq_intake_receipt_source_message"
        ),
    )
    op.create_index(
        "ix_intake_receipts_job_created",
        "application_intake_receipts",
        ["job_id", "created_at"],
    )
    op.create_index(
        "ix_intake_receipts_task_id", "application_intake_receipts", ["processing_task_id"]
    )
    op.create_index(
        "ix_intake_receipts_archive_status", "application_intake_receipts", ["archive_status"]
    )


def downgrade() -> None:
    op.drop_index("ix_intake_receipts_archive_status", table_name="application_intake_receipts")
    op.drop_index("ix_intake_receipts_task_id", table_name="application_intake_receipts")
    op.drop_index("ix_intake_receipts_job_created", table_name="application_intake_receipts")
    op.drop_table("application_intake_receipts")
    op.drop_constraint("processing_job_type", "processing_jobs", type_="check")
    op.create_check_constraint(
        "processing_job_type",
        "processing_jobs",
        "job_type IN ('COMPANY_DOCUMENT_INGESTION', 'ASSISTANT_TURN', "
        "'JOB_PUBLICATION', 'PROCESS_CANDIDATE_DOCUMENT', "
        "'SCREEN_CANDIDATE_APPLICATION')",
    )
    op.drop_constraint("uq_job_publication_provider_attempt", "job_publications", type_="unique")
    op.drop_column("job_publications", "published_jd_version")
    op.create_unique_constraint(
        "uq_job_publication_attempt", "job_publications", ["job_id", "attempt_number"]
    )
    op.create_unique_constraint(
        "job_publications_processing_task_id_key", "job_publications", ["processing_task_id"]
    )
    op.drop_column("jobs", "drive_folder_created_at")
    op.drop_column("jobs", "drive_folder_url")
    op.drop_column("jobs", "drive_folder_id")
    op.drop_index("ix_jobs_application_code", table_name="jobs")
    op.drop_column("jobs", "application_code")
