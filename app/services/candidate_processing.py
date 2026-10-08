import logging
from collections.abc import Callable
from uuid import UUID, uuid4

from app.adapters.document_parser import ExtractedSection
from app.adapters.document_storage import DocumentStorage
from app.adapters.embeddings import EmbeddingAdapter
from app.adapters.llm import LLMAdapter
from app.errors import (
    ApplicationNotFoundError,
    CandidateDocumentNotFoundError,
    CandidateProcessingError,
    CandidateProcessingStateError,
    CandidateProfileValidationError,
    EmbeddingProviderError,
    JobNotFoundError,
    RecrUnionError,
)
from app.models.applications import (
    CandidateCVChunk,
    CandidateDocument,
    CandidateDocumentProcessingStatus,
    CandidateProfile,
)
from app.models.processing_jobs import ProcessingJob, ProcessingJobType
from app.repositories.candidate_processing import (
    CandidateProcessingRepository,
    task_is_active,
)
from app.repositories.jobs import JobRepository
from app.schemas.applications import (
    CandidateProcessingBatchResponse,
    CandidateProcessingStartResponse,
)
from app.schemas.candidate_profiles import (
    CandidateProfileData,
    CandidateProfileEvidence,
    CandidateProfileExtractionRequest,
)
from app.services.candidate_pdf_extraction import CandidatePDFExtractor, ExtractedCVPage
from app.services.document_chunking import chunk_sections

ProgressReporter = Callable[[int, str], None]
logger = logging.getLogger(__name__)


class CandidateProcessingService:
    """Queue and produce evidence-backed candidate profiles from imported CVs."""

    def __init__(
        self,
        job_repository: JobRepository,
        repository: CandidateProcessingRepository,
        storage: DocumentStorage,
        extractor: CandidatePDFExtractor,
        llm_adapter: LLMAdapter,
        embedding_adapter: EmbeddingAdapter,
        *,
        worker_max_attempts: int,
        minimum_text_characters: int,
    ) -> None:
        self._job_repository = job_repository
        self._repository = repository
        self._storage = storage
        self._extractor = extractor
        self._llm_adapter = llm_adapter
        self._embedding_adapter = embedding_adapter
        self._worker_max_attempts = worker_max_attempts
        self._minimum_text_characters = minimum_text_characters

    def queue(self, job_id: UUID, application_id: UUID) -> CandidateProcessingStartResponse:
        self._require_job(job_id)
        document = self._repository.lock_application_document(job_id, application_id)
        if document is None:
            raise ApplicationNotFoundError
        return self._queue_document(document)

    def queue_pending(self, job_id: UUID) -> CandidateProcessingBatchResponse:
        self._require_job(job_id)
        tasks = [
            self._queue_document(document)
            for document in self._repository.list_pending_for_job(job_id)
        ]
        return CandidateProcessingBatchResponse(queued=len(tasks), tasks=tasks)

    async def process(self, document_id: UUID, report: ProgressReporter) -> None:
        document = self._repository.get_document(document_id)
        if document is None:
            raise CandidateDocumentNotFoundError
        self._repository.begin_processing(document)
        try:
            report(10, "Loading the managed candidate document")
            with self._storage.open(document.storage_key) as source:
                report(20, "Extracting page-aware CV text")
                pages = self._extractor.extract(source)
            if self._usable_character_count(pages) < self._minimum_text_characters:
                self._repository.mark_needs_review(
                    document,
                    code="INSUFFICIENT_EXTRACTABLE_TEXT",
                    message=(
                        "The CV does not contain enough extractable text. "
                        "Scanned documents require manual review."
                    ),
                )
                report(90, "Manual review is needed because extractable text is insufficient")
                return

            report(35, "Creating page-linked evidence chunks")
            chunks = self._build_chunks(document, pages)
            if not chunks:
                self._repository.mark_needs_review(
                    document,
                    code="INSUFFICIENT_EXTRACTABLE_TEXT",
                    message="The CV does not contain useful extractable evidence.",
                )
                report(90, "Manual review is needed because no evidence chunks were created")
                return
            document = self._repository.replace_chunks(document, chunks)

            report(50, "Building the structured candidate profile")
            extraction = await self._llm_adapter.extract_candidate_profile(
                CandidateProfileExtractionRequest(
                    document_id=document.id,
                    candidate_reference=document.application.candidate.display_reference,
                    evidence=[
                        CandidateProfileEvidence(
                            evidence_id=chunk.id,
                            page_number=chunk.page_number,
                            content=chunk.content,
                        )
                        for chunk in document.chunks
                    ],
                )
            )
            self._validate_evidence_references(extraction.profile, document.chunks)

            report(70, "Embedding candidate evidence locally")
            embeddings = self._embedding_adapter.embed_documents(
                [chunk.content for chunk in document.chunks]
            )
            self._validate_embeddings(embeddings, len(document.chunks))

            report(88, "Persisting candidate profile and evidence vectors")
            profile = CandidateProfile(
                candidate_id=document.application.candidate_id,
                application_id=document.application_id,
                source_document_id=document.id,
                structured_json=extraction.profile.model_dump(mode="json"),
                provider=extraction.provider,
                model=extraction.model,
            )
            self._repository.complete(
                document,
                profile,
                embeddings,
                embedding_model=self._embedding_adapter.model_name,
                embedding_dimension=self._embedding_adapter.dimension,
            )
            report(95, "Candidate profile and evidence are ready")
        except RecrUnionError as error:
            self._repository.mark_failed(
                document,
                code=error.code,
                message=str(error),
            )
            raise
        except Exception as error:
            safe_error = CandidateProcessingError("The candidate document could not be processed.")
            self._repository.mark_failed(
                document,
                code=safe_error.code,
                message=str(safe_error),
            )
            logger.warning(
                "Candidate document processing failed",
                extra={
                    "document_id": str(document.id),
                    "operation": "candidate_document_processing",
                    "error_code": safe_error.code,
                },
            )
            raise safe_error from error

    def _queue_document(
        self,
        document: CandidateDocument,
    ) -> CandidateProcessingStartResponse:
        if task_is_active(self._repository.current_task(document)):
            raise CandidateProcessingStateError(
                "Candidate document processing is already queued or running."
            )
        if document.processing_status == CandidateDocumentProcessingStatus.READY:
            raise CandidateProcessingStateError(
                "The candidate document has already been processed."
            )
        if document.processing_status == CandidateDocumentProcessingStatus.PROCESSING:
            raise CandidateProcessingStateError("Candidate document processing is already running.")
        task = ProcessingJob(
            id=uuid4(),
            job_type=ProcessingJobType.PROCESS_CANDIDATE_DOCUMENT,
            entity_type="CANDIDATE_DOCUMENT",
            entity_id=document.id,
            max_attempts=self._worker_max_attempts,
            progress_message="Waiting to process the candidate CV",
        )
        self._repository.queue(document, task)
        return CandidateProcessingStartResponse(
            application_id=document.application_id,
            document_id=document.id,
            processing_status=document.processing_status,
            task_id=task.id,
        )

    def _require_job(self, job_id: UUID) -> None:
        if self._job_repository.get(job_id) is None:
            raise JobNotFoundError(job_id)

    @staticmethod
    def _usable_character_count(pages: list[ExtractedCVPage]) -> int:
        return sum(sum(character.isalnum() for character in page.text) for page in pages)

    @staticmethod
    def _build_chunks(
        document: CandidateDocument,
        pages: list[ExtractedCVPage],
    ) -> list[CandidateCVChunk]:
        sections = [
            ExtractedSection(
                text=page.text,
                page_number=page.page_number,
            )
            for page in pages
            if page.text
        ]
        return [
            CandidateCVChunk(
                id=uuid4(),
                document_id=document.id,
                chunk_index=index,
                page_number=chunk.page_number or 1,
                content=chunk.content,
            )
            for index, chunk in enumerate(chunk_sections(sections))
        ]

    @staticmethod
    def _validate_evidence_references(
        profile: CandidateProfileData,
        chunks: list[CandidateCVChunk],
    ) -> None:
        allowed_ids = {chunk.id for chunk in chunks}
        items = [
            *([profile.summary] if profile.summary is not None else []),
            *profile.contact_details,
            *profile.education,
            *profile.work_history,
            *profile.skills,
            *profile.certifications,
            *profile.projects,
            *profile.technologies_tools,
        ]
        referenced_ids = {evidence_id for item in items for evidence_id in item.evidence_chunk_ids}
        if not referenced_ids or not referenced_ids.issubset(allowed_ids):
            raise CandidateProfileValidationError(
                "The generated candidate profile contains invalid evidence references."
            )

    def _validate_embeddings(
        self,
        embeddings: list[list[float]],
        expected_count: int,
    ) -> None:
        if len(embeddings) != expected_count or any(
            len(vector) != self._embedding_adapter.dimension for vector in embeddings
        ):
            raise EmbeddingProviderError(
                "The local embedding provider returned invalid candidate vectors."
            )
