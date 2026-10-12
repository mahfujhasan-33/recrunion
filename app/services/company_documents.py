import re
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from app.adapters.document_parser import CompanyDocumentParser
from app.adapters.document_storage import LocalDocumentStorage
from app.errors import (
    CompanyDocumentBusyError,
    CompanyDocumentDuplicateError,
    CompanyDocumentNotFoundError,
    CompanyDocumentTooLargeError,
    CompanyDocumentValidationError,
    DocumentStorageTooLargeError,
    DocumentStorageValidationError,
)
from app.models.company_documents import (
    CompanyDocument,
    CompanyDocumentStatus,
    CompanyDocumentType,
)
from app.models.processing_jobs import ProcessingJob, ProcessingJobType
from app.repositories.company_documents import CompanyDocumentRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.schemas.company_documents import CompanyDocumentResponse, CompanyDocumentUploadResponse


class CompanyDocumentService:
    def __init__(
        self,
        repository: CompanyDocumentRepository,
        task_repository: ProcessingJobRepository,
        storage: LocalDocumentStorage,
        parser: CompanyDocumentParser,
        *,
        worker_max_attempts: int,
    ) -> None:
        self._repository = repository
        self._task_repository = task_repository
        self._storage = storage
        self._parser = parser
        self._worker_max_attempts = worker_max_attempts

    def upload(
        self,
        *,
        filename: str,
        content_type: str,
        document_type: CompanyDocumentType,
        source: BinaryIO,
    ) -> CompanyDocumentUploadResponse:
        safe_filename = _safe_filename(filename)
        suffix = Path(safe_filename).suffix.casefold()
        try:
            stored = self._storage.save(source, suffix)
        except DocumentStorageTooLargeError as error:
            raise CompanyDocumentTooLargeError(
                "Company document exceeds the configured upload limit."
            ) from error
        except DocumentStorageValidationError as error:
            raise CompanyDocumentValidationError(str(error)) from error
        document: CompanyDocument | None = None
        try:
            path = self._storage.path_for(stored.storage_key)
            self._parser.validate(path, suffix, content_type)
            if self._repository.find_by_sha256(stored.sha256) is not None:
                raise CompanyDocumentDuplicateError(
                    "The same company document has already been uploaded."
                )
            document = self._repository.create(
                CompanyDocument(
                    original_filename=safe_filename,
                    storage_key=stored.storage_key,
                    document_type=document_type,
                    content_type=content_type,
                    size_bytes=stored.size_bytes,
                    sha256=stored.sha256,
                    status=CompanyDocumentStatus.UPLOADED,
                )
            )
            task = self._task_repository.create(
                ProcessingJob(
                    job_type=ProcessingJobType.COMPANY_DOCUMENT_INGESTION,
                    entity_type="COMPANY_DOCUMENT",
                    entity_id=document.id,
                    max_attempts=self._worker_max_attempts,
                    progress_message="Waiting for document processing",
                )
            )
        except Exception:
            if document is not None:
                self._repository.delete(document)
            self._storage.delete(stored.storage_key)
            raise
        return CompanyDocumentUploadResponse(
            document=self.to_response(document),
            task_id=task.id,
        )

    def list_documents(self) -> list[CompanyDocumentResponse]:
        return [self.to_response(document) for document in self._repository.list_all()]

    def get_document(self, document_id: UUID) -> CompanyDocumentResponse:
        return self.to_response(self._get(document_id))

    def delete_document(self, document_id: UUID) -> None:
        document = self._get(document_id)
        if document.status == CompanyDocumentStatus.PROCESSING:
            raise CompanyDocumentBusyError(
                "A company document cannot be deleted while it is processing."
            )
        document.status = CompanyDocumentStatus.DELETING
        self._repository.update(document)
        self._task_repository.delete_queued_for_entity("COMPANY_DOCUMENT", document.id)
        try:
            self._storage.delete(document.storage_key)
        except OSError as error:
            document.status = CompanyDocumentStatus.DELETE_FAILED
            document.safe_error_code = "DOCUMENT_FILE_DELETE_FAILED"
            document.safe_error_message = "The managed document file could not be removed."
            self._repository.update(document)
            raise CompanyDocumentValidationError(
                "The company document could not be removed."
            ) from error
        self._repository.delete(document)

    def _get(self, document_id: UUID) -> CompanyDocument:
        document = self._repository.get(document_id)
        if document is None:
            raise CompanyDocumentNotFoundError
        return document

    @staticmethod
    def to_response(document: CompanyDocument) -> CompanyDocumentResponse:
        return CompanyDocumentResponse(
            id=document.id,
            original_filename=document.original_filename,
            document_type=document.document_type,
            content_type=document.content_type,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
            status=document.status,
            safe_error_code=document.safe_error_code,
            safe_error_message=document.safe_error_message,
            embedding_model=document.embedding_model,
            embedding_dimension=document.embedding_dimension,
            chunk_count=document.chunk_count,
            uploaded_at=document.uploaded_at,
            processing_started_at=document.processing_started_at,
            processed_at=document.processed_at,
            updated_at=document.updated_at,
        )


def _safe_filename(filename: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", "", Path(filename).name).strip()
    if not value or len(value) > 255:
        raise CompanyDocumentValidationError("Document filename is invalid.")
    return value
