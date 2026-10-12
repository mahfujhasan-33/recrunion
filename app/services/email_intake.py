import hashlib
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID

from app.adapters.gmail import GmailApplicationMessage, GmailApplicationSource
from app.errors import (
    DriveError,
    EmailIntakeStateError,
    JobNotFoundError,
)
from app.models.applications import (
    ApplicationIntakeReceipt,
    ApplicationSource,
    DriveArchiveStatus,
    IntakeReceiptStatus,
)
from app.models.jobs import Job
from app.models.processing_jobs import ProcessingJob, ProcessingJobType
from app.repositories.email_intake import EmailIntakeRepository
from app.schemas.applications import ApplicationUploadOutcome
from app.schemas.email_intake import (
    EmailIntakeReceiptResponse,
    EmailSyncQueuedResponse,
    EmailSyncStatusResponse,
    EmailSyncSummary,
)
from app.services.application_intake import ApplicationIntakeService, IncomingCandidateDocument
from app.services.drive_archive import DriveArchiveService

logger = logging.getLogger(__name__)


class EmailApplicationIntakeService:
    """Import exact-subject Gmail applications through the shared intake service."""

    def __init__(
        self,
        repository: EmailIntakeRepository,
        intake_service: ApplicationIntakeService,
        source: GmailApplicationSource,
        archive_service: DriveArchiveService,
        *,
        worker_max_attempts: int,
        sync_interval_seconds: int,
    ) -> None:
        self._repository = repository
        self._intake = intake_service
        self._source = source
        self._archive = archive_service
        self._worker_max_attempts = worker_max_attempts
        self._sync_interval_seconds = sync_interval_seconds

    def queue(self, job_id: UUID) -> EmailSyncQueuedResponse:
        job = self._repository.lock_job(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        active = self._repository.active_task_for_job(job_id)
        if active is not None:
            return EmailSyncQueuedResponse(task_id=active.id, status=active.status)
        task = ProcessingJob(
            job_type=ProcessingJobType.SYNC_EMAIL_APPLICATIONS,
            entity_type="JOB",
            entity_id=job_id,
            max_attempts=self._worker_max_attempts,
            progress_message="Email sync queued",
        )
        self._repository.queue_task(task)
        return EmailSyncQueuedResponse(task_id=task.id, status=task.status)

    def queue_due(self) -> int:
        now = datetime.now(UTC)
        queued = 0
        for job in self._repository.list_eligible_jobs():
            latest = self._repository.latest_task_for_job(job.id)
            if self._repository.task_is_due(
                latest, now=now, interval_seconds=self._sync_interval_seconds
            ):
                self.queue(job.id)
                queued += 1
        return queued

    async def process(
        self,
        task_id: UUID,
        report: Callable[[int, str], None],
    ) -> EmailSyncSummary:
        task = self._repository.get_task(task_id)
        if task is None or task.job_type != ProcessingJobType.SYNC_EMAIL_APPLICATIONS:
            raise EmailIntakeStateError("Email synchronization task was not found.")
        job = self._repository.get_job(task.entity_id)
        if job is None:
            raise JobNotFoundError(task.entity_id)
        report(10, "Searching the application mailbox")
        messages = await self._source.fetch_job_applications(job.application_code)
        total = max(len(messages), 1)
        for index, message in enumerate(messages, start=1):
            report(15 + int(index / total * 70), f"Processing message {index} of {len(messages)}")
            try:
                await self._process_message(job, task.id, message)
            except Exception:
                self._repository.rollback()
                receipt = self._repository.find_message(message.message_id)
                if receipt is None:
                    receipt = ApplicationIntakeReceipt(
                        job_id=job.id,
                        processing_task_id=task.id,
                        source_type=ApplicationSource.EMAIL,
                        external_message_id=message.message_id,
                        received_at=message.received_at,
                        status=IntakeReceiptStatus.FAILED,
                        safe_error_code="EMAIL_MESSAGE_INTAKE_FAILED",
                        safe_error_message="This application email could not be imported.",
                    )
                    self._repository.create_receipt(receipt)
                else:
                    receipt.status = IntakeReceiptStatus.FAILED
                    receipt.safe_error_code = "EMAIL_MESSAGE_INTAKE_FAILED"
                    receipt.safe_error_message = "This application email could not be imported."
                    self._repository.update_receipt(receipt)
                logger.warning(
                    "Application email intake failed",
                    extra={"receipt_id": str(receipt.id), "job_id": str(job.id)},
                )
        report(90, "Finalizing email intake results")
        return self._summary(task.id, matched=len(messages))

    def status(self, job_id: UUID) -> EmailSyncStatusResponse:
        if self._repository.get_job(job_id) is None:
            raise JobNotFoundError(job_id)
        task = self._repository.latest_task_for_job(job_id)
        if task is None:
            return EmailSyncStatusResponse(
                task_id=None,
                task_status=None,
                progress=None,
                progress_message=None,
                last_sync_at=None,
                summary=None,
                receipts=[],
            )
        receipts = self._repository.list_receipts_for_task(task.id)
        return EmailSyncStatusResponse(
            task_id=task.id,
            task_status=task.status,
            progress=task.progress,
            progress_message=task.progress_message,
            last_sync_at=task.completed_at,
            summary=self._summary(task.id, matched=len(receipts)),
            receipts=[
                EmailIntakeReceiptResponse.model_validate(item, from_attributes=True)
                for item in receipts
            ],
        )

    async def _process_message(
        self,
        job: Job,
        task_id: UUID,
        message: GmailApplicationMessage,
    ) -> None:
        receipt = self._repository.find_message(message.message_id)
        if receipt is not None:
            receipt.processing_task_id = task_id
            if receipt.archive_status == DriveArchiveStatus.RETRY_PENDING:
                self._repository.update_receipt(receipt)
                with suppress(DriveError):
                    await self._archive.archive(receipt.id)
                return
            if receipt.status not in {
                IntakeReceiptStatus.RECEIVED,
                IntakeReceiptStatus.ATTACHMENT_FETCH_FAILED,
                IntakeReceiptStatus.FAILED,
            }:
                self._repository.update_receipt(receipt)
                return
            receipt.safe_error_code = None
            receipt.safe_error_message = None
        else:
            receipt = ApplicationIntakeReceipt(
                job_id=job.id,
                processing_task_id=task_id,
                source_type=ApplicationSource.EMAIL,
                external_message_id=message.message_id,
                received_at=message.received_at,
            )
            self._repository.create_receipt(receipt)
        pdfs = [
            attachment
            for attachment in message.attachments
            if Path(attachment.filename).suffix.casefold() == ".pdf"
            and attachment.mime_type.casefold() == "application/pdf"
        ]
        if not message.attachments:
            receipt.status = IntakeReceiptStatus.NO_CV_ATTACHMENT
        elif not pdfs:
            receipt.status = IntakeReceiptStatus.UNSUPPORTED_FILE_TYPE
        elif len(pdfs) > 1:
            receipt.status = IntakeReceiptStatus.MULTIPLE_PDF_ATTACHMENTS
        else:
            pdf = pdfs[0]
            receipt.external_attachment_id = pdf.attachment_id
            receipt.original_filename = pdf.filename
            receipt.mime_type = pdf.mime_type
            receipt.file_size = pdf.size
            if pdf.fetch_error or pdf.content is None:
                receipt.status = IntakeReceiptStatus.ATTACHMENT_FETCH_FAILED
            else:
                receipt.sha256 = hashlib.sha256(pdf.content).hexdigest()
                self._repository.update_receipt(receipt)
                result = self._intake.intake_document(
                    job,
                    IncomingCandidateDocument(pdf.filename, pdf.mime_type, BytesIO(pdf.content)),
                    source=ApplicationSource.EMAIL,
                )
                if result.outcome == ApplicationUploadOutcome.IMPORTED and result.application:
                    receipt.status = IntakeReceiptStatus.IMPORTED
                    receipt.candidate_document_id = result.application.document_id
                    receipt.archive_status = DriveArchiveStatus.PENDING
                elif result.outcome == ApplicationUploadOutcome.SKIPPED_DUPLICATE:
                    receipt.status = IntakeReceiptStatus.SKIPPED_DUPLICATE
                elif result.outcome == ApplicationUploadOutcome.INVALID_FILE:
                    receipt.status = self._invalid_status(result.message)
                    receipt.safe_error_message = result.message
                else:
                    receipt.status = IntakeReceiptStatus.FAILED
                    receipt.safe_error_message = result.message
        self._repository.update_receipt(receipt)
        if receipt.status == IntakeReceiptStatus.IMPORTED:
            with suppress(DriveError):
                await self._archive.archive(receipt.id)

    def _summary(self, task_id: UUID, *, matched: int) -> EmailSyncSummary:
        receipts = self._repository.list_receipts_for_task(task_id)
        return EmailSyncSummary(
            matched=matched,
            imported=sum(item.status == IntakeReceiptStatus.IMPORTED for item in receipts),
            duplicates=sum(
                item.status == IntakeReceiptStatus.SKIPPED_DUPLICATE for item in receipts
            ),
            needs_review=sum(
                item.status == IntakeReceiptStatus.MULTIPLE_PDF_ATTACHMENTS for item in receipts
            ),
            rejected=sum(
                item.status
                in {
                    IntakeReceiptStatus.NO_CV_ATTACHMENT,
                    IntakeReceiptStatus.UNSUPPORTED_FILE_TYPE,
                    IntakeReceiptStatus.INVALID_PDF,
                    IntakeReceiptStatus.OVERSIZED_FILE,
                    IntakeReceiptStatus.ATTACHMENT_FETCH_FAILED,
                    IntakeReceiptStatus.FAILED,
                }
                for item in receipts
            ),
            drive_archived=sum(
                item.archive_status == DriveArchiveStatus.ARCHIVED for item in receipts
            ),
            drive_archive_failed=sum(
                item.archive_status == DriveArchiveStatus.RETRY_PENDING for item in receipts
            ),
        )

    @staticmethod
    def _invalid_status(message: str) -> IntakeReceiptStatus:
        if "upload limit" in message.casefold() or "exceeds" in message.casefold():
            return IntakeReceiptStatus.OVERSIZED_FILE
        return IntakeReceiptStatus.INVALID_PDF
