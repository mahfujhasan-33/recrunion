import hashlib
import logging
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.adapters.document_storage import DocumentStorage, StoredDocument
from app.errors import (
    ApplicationNotFoundError,
    DocumentStorageTooLargeError,
    DocumentStorageValidationError,
    JobNotFoundError,
)
from app.models.applications import (
    ApplicationSource,
    ApplicationStatus,
    Candidate,
    CandidateDocument,
    JobApplication,
)
from app.models.jobs import Job
from app.repositories.applications import ApplicationRepository
from app.repositories.jobs import JobRepository
from app.schemas.applications import (
    ApplicationBatchUploadResponse,
    ApplicationResponse,
    ApplicationUploadFileResult,
    ApplicationUploadOutcome,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncomingCandidateDocument:
    filename: str
    content_type: str
    source: BinaryIO


class InvalidCandidateDocumentError(ValueError):
    """Internal validation error translated into one batch-file result."""


class ApplicationIntakeService:
    """Validate and persist recruiter-uploaded candidate applications."""

    def __init__(
        self,
        job_repository: JobRepository,
        application_repository: ApplicationRepository,
        storage: DocumentStorage,
        *,
        max_file_size_bytes: int,
    ) -> None:
        self._job_repository = job_repository
        self._application_repository = application_repository
        self._storage = storage
        self._max_file_size_bytes = max_file_size_bytes

    def upload_batch(
        self,
        job_id: UUID,
        uploads: list[IncomingCandidateDocument],
    ) -> ApplicationBatchUploadResponse:
        job = self._get_job(job_id)
        results = [self._process_file(job, upload) for upload in uploads]
        return ApplicationBatchUploadResponse(
            received=len(results),
            imported=self._count(results, ApplicationUploadOutcome.IMPORTED),
            duplicates=self._count(results, ApplicationUploadOutcome.SKIPPED_DUPLICATE),
            invalid=self._count(results, ApplicationUploadOutcome.INVALID_FILE),
            failed=self._count(results, ApplicationUploadOutcome.FAILED),
            files=results,
        )

    def list_applications(self, job_id: UUID) -> list[ApplicationResponse]:
        self._get_job(job_id)
        return [
            self.to_response(application)
            for application in self._application_repository.list_for_job(job_id)
        ]

    def get_application(self, job_id: UUID, application_id: UUID) -> ApplicationResponse:
        self._get_job(job_id)
        application = self._application_repository.get_for_job(job_id, application_id)
        if application is None:
            raise ApplicationNotFoundError
        return self.to_response(application)

    def _process_file(
        self,
        job: Job,
        upload: IncomingCandidateDocument,
    ) -> ApplicationUploadFileResult:
        display_filename = "Unnamed PDF"
        try:
            display_filename = _safe_filename(upload.filename)
            content, sha256 = self._validate_and_hash(display_filename, upload)
            if (
                self._application_repository.find_document_by_job_and_sha256(job.id, sha256)
                is not None
            ):
                return ApplicationUploadFileResult(
                    filename=display_filename,
                    outcome=ApplicationUploadOutcome.SKIPPED_DUPLICATE,
                    message="This CV has already been uploaded for this job.",
                )
            application = self._persist(job, display_filename, content, sha256)
            return ApplicationUploadFileResult(
                filename=display_filename,
                outcome=ApplicationUploadOutcome.IMPORTED,
                message="CV imported successfully.",
                application=self.to_response(application),
            )
        except InvalidCandidateDocumentError as error:
            return ApplicationUploadFileResult(
                filename=display_filename,
                outcome=ApplicationUploadOutcome.INVALID_FILE,
                message=str(error),
            )
        except (DocumentStorageTooLargeError, DocumentStorageValidationError) as error:
            return ApplicationUploadFileResult(
                filename=display_filename,
                outcome=ApplicationUploadOutcome.INVALID_FILE,
                message=str(error),
            )
        except Exception:
            logger.warning(
                "Candidate CV import failed",
                extra={"job_id": str(job.id), "operation": "candidate_cv_import"},
            )
            return ApplicationUploadFileResult(
                filename=display_filename,
                outcome=ApplicationUploadOutcome.FAILED,
                message="The CV could not be imported.",
            )

    def _validate_and_hash(
        self,
        filename: str,
        upload: IncomingCandidateDocument,
    ) -> tuple[bytes, str]:
        if Path(filename).suffix.casefold() != ".pdf":
            raise InvalidCandidateDocumentError("Only PDF CV files are supported.")
        if upload.content_type.casefold() != "application/pdf":
            raise InvalidCandidateDocumentError("The file content type must be application/pdf.")
        content = upload.source.read(self._max_file_size_bytes + 1)
        if not content:
            raise InvalidCandidateDocumentError("The PDF file is empty.")
        if len(content) > self._max_file_size_bytes:
            raise InvalidCandidateDocumentError("The PDF exceeds the configured upload limit.")
        if not content.startswith(b"%PDF-"):
            raise InvalidCandidateDocumentError("The file content is not a valid PDF.")
        try:
            reader = PdfReader(BytesIO(content), strict=False)
            if reader.is_encrypted:
                raise InvalidCandidateDocumentError("Encrypted PDF files are not supported.")
            if len(reader.pages) == 0:
                raise InvalidCandidateDocumentError("The PDF must contain at least one page.")
        except PdfReadError as error:
            raise InvalidCandidateDocumentError("The file content is not a valid PDF.") from error
        return content, hashlib.sha256(content).hexdigest()

    def _persist(
        self,
        job: Job,
        original_filename: str,
        content: bytes,
        sha256: str,
    ) -> JobApplication:
        stored: StoredDocument | None = None
        try:
            stored = self._storage.save(BytesIO(content), ".pdf", namespace=job.code)
            if stored.sha256 != sha256:
                raise DocumentStorageValidationError("Stored document integrity check failed.")
            candidate_id = uuid4()
            application_id = uuid4()
            candidate = Candidate(
                id=candidate_id,
                display_reference=f"Candidate {candidate_id.hex[:8].upper()}",
            )
            application = JobApplication(
                id=application_id,
                job_id=job.id,
                candidate_id=candidate.id,
                source=ApplicationSource.MANUAL_UPLOAD,
                status=ApplicationStatus.IMPORTED,
            )
            document = CandidateDocument(
                application_id=application.id,
                job_id=job.id,
                original_filename=original_filename,
                storage_key=stored.storage_key,
                mime_type="application/pdf",
                file_size=stored.size_bytes,
                sha256=sha256,
            )
            return self._application_repository.create(candidate, application, document)
        except Exception:
            if stored is not None:
                try:
                    self._storage.delete(stored.storage_key)
                except OSError:
                    logger.warning(
                        "Candidate CV cleanup failed",
                        extra={"job_id": str(job.id), "operation": "candidate_cv_cleanup"},
                    )
            raise

    def _get_job(self, job_id: UUID) -> Job:
        job = self._job_repository.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    @staticmethod
    def to_response(application: JobApplication) -> ApplicationResponse:
        return ApplicationResponse(
            id=application.id,
            reference=f"APP-{application.id.hex[:8].upper()}",
            candidate_id=application.candidate.id,
            candidate_display_reference=application.candidate.display_reference,
            original_filename=application.document.original_filename,
            file_size=application.document.file_size,
            source=application.source,
            status=application.status,
            imported_at=application.created_at,
        )

    @staticmethod
    def _count(
        results: list[ApplicationUploadFileResult],
        outcome: ApplicationUploadOutcome,
    ) -> int:
        return sum(result.outcome == outcome for result in results)


def _safe_filename(filename: str) -> str:
    basename = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    value = re.sub(r"[\x00-\x1f\x7f]", "", basename).strip()
    if not value or len(value) > 255:
        raise InvalidCandidateDocumentError("The PDF filename is invalid.")
    return value
