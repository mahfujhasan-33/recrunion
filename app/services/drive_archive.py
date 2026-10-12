import re
from datetime import UTC, datetime
from uuid import UUID

from app.adapters.document_storage import DocumentStorage
from app.adapters.drive import DriveArchiveAdapter
from app.errors import DriveError, IntakeReceiptNotFoundError
from app.models.applications import DriveArchiveStatus
from app.repositories.email_intake import EmailIntakeRepository
from app.schemas.email_intake import EmailIntakeReceiptResponse


class DriveArchiveService:
    """Mirror an imported email CV to a stable per-job Drive folder."""

    def __init__(
        self,
        repository: EmailIntakeRepository,
        storage: DocumentStorage,
        adapter: DriveArchiveAdapter,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._adapter = adapter

    async def archive(self, receipt_id: UUID) -> EmailIntakeReceiptResponse:
        receipt = self._repository.get_receipt(receipt_id)
        if receipt is None:
            raise IntakeReceiptNotFoundError
        if receipt.candidate_document_id is None:
            raise DriveError("Only successfully imported CVs can be archived.")
        job = self._repository.get_job(receipt.job_id)
        document = self._repository.get_document(receipt.candidate_document_id)
        if job is None or document is None:
            raise DriveError("The local CV record required for archiving is unavailable.")

        try:
            if not job.drive_folder_id:
                folder = await self._adapter.create_folder(
                    f"{job.application_code} - {self._safe_title(job.title)}"
                )
                job.drive_folder_id = folder.id
                job.drive_folder_url = folder.url
                job.drive_folder_created_at = datetime.now(UTC)
                self._repository.save_job(job)
            with self._storage.open(document.storage_key) as source:
                archived = await self._adapter.archive_pdf(
                    job.drive_folder_id,
                    f"APP-{document.application_id.hex[:8].upper()}.pdf",
                    source.read(),
                )
            receipt.drive_file_id = archived.id
            receipt.drive_file_url = archived.url
            receipt.archive_status = DriveArchiveStatus.ARCHIVED
            receipt.safe_error_code = None
            receipt.safe_error_message = None
        except DriveError as error:
            receipt.archive_status = DriveArchiveStatus.RETRY_PENDING
            receipt.safe_error_code = error.code
            receipt.safe_error_message = str(error)
            self._repository.update_receipt(receipt)
            raise
        self._repository.update_receipt(receipt)
        return EmailIntakeReceiptResponse.model_validate(receipt, from_attributes=True)

    @staticmethod
    def _safe_title(title: str) -> str:
        value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", title)
        return " ".join(value.split())[:100] or "Job"
