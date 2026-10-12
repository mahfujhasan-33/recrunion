from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models.applications import (
    CandidateCVChunk,
    CandidateDocument,
    CandidateDocumentProcessingStatus,
    CandidateProfile,
    JobApplication,
)
from app.models.processing_jobs import ProcessingJob, ProcessingJobStatus


@dataclass(frozen=True)
class CandidateEvidenceMatch:
    chunk_id: UUID
    document_id: UUID
    page_number: int
    content: str
    similarity: float


class CandidateProcessingRepository:
    """Persist candidate-document processing state and derived evidence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_document(self, document_id: UUID) -> CandidateDocument | None:
        statement = (
            select(CandidateDocument)
            .where(CandidateDocument.id == document_id)
            .options(
                selectinload(CandidateDocument.application).selectinload(JobApplication.candidate),
                selectinload(CandidateDocument.chunks),
                selectinload(CandidateDocument.profile),
            )
        )
        return self._session.scalar(statement)

    def lock_application_document(
        self,
        job_id: UUID,
        application_id: UUID,
    ) -> CandidateDocument | None:
        statement = (
            select(CandidateDocument)
            .where(
                CandidateDocument.job_id == job_id,
                CandidateDocument.application_id == application_id,
            )
            .options(
                selectinload(CandidateDocument.application).selectinload(JobApplication.candidate)
            )
            .with_for_update()
        )
        return self._session.scalar(statement)

    def list_pending_for_job(self, job_id: UUID) -> list[CandidateDocument]:
        statement = (
            select(CandidateDocument)
            .where(
                CandidateDocument.job_id == job_id,
                CandidateDocument.processing_status == CandidateDocumentProcessingStatus.IMPORTED,
            )
            .options(
                selectinload(CandidateDocument.application).selectinload(JobApplication.candidate)
            )
            .order_by(CandidateDocument.created_at, CandidateDocument.id)
            .with_for_update(skip_locked=True)
        )
        return list(self._session.scalars(statement))

    def current_task(self, document: CandidateDocument) -> ProcessingJob | None:
        if document.processing_task_id is None:
            return None
        return self._session.get(ProcessingJob, document.processing_task_id)

    def queue(self, document: CandidateDocument, task: ProcessingJob) -> None:
        document.processing_status = CandidateDocumentProcessingStatus.QUEUED
        document.processing_task_id = task.id
        document.safe_error_code = None
        document.safe_error_message = None
        self._session.add(task)
        self._commit()

    def begin_processing(self, document: CandidateDocument) -> None:
        document.processing_status = CandidateDocumentProcessingStatus.PROCESSING
        document.processing_started_at = datetime.now(UTC)
        document.processed_at = None
        document.safe_error_code = None
        document.safe_error_message = None
        self._commit()

    def replace_chunks(
        self,
        document: CandidateDocument,
        chunks: list[CandidateCVChunk],
    ) -> CandidateDocument:
        self._delete_derived(document.id)
        self._session.add_all(chunks)
        document.chunk_count = len(chunks)
        document.embedding_model = None
        document.embedding_dimension = None
        self._commit()
        return self.get_document(document.id) or document

    def complete(
        self,
        document: CandidateDocument,
        profile: CandidateProfile,
        embeddings: list[list[float]],
        *,
        embedding_model: str,
        embedding_dimension: int,
    ) -> None:
        if len(document.chunks) != len(embeddings):
            raise ValueError("Candidate chunk and embedding counts do not match.")
        for chunk, embedding in zip(document.chunks, embeddings, strict=True):
            chunk.embedding = embedding
        document.profile = profile
        document.embedding_model = embedding_model
        document.embedding_dimension = embedding_dimension
        document.chunk_count = len(document.chunks)
        document.processing_status = CandidateDocumentProcessingStatus.READY
        document.processed_at = datetime.now(UTC)
        document.safe_error_code = None
        document.safe_error_message = None
        self._commit()

    def mark_needs_review(
        self,
        document: CandidateDocument,
        *,
        code: str,
        message: str,
    ) -> None:
        self._delete_derived(document.id)
        document.chunk_count = 0
        document.embedding_model = None
        document.embedding_dimension = None
        document.processing_status = CandidateDocumentProcessingStatus.NEEDS_REVIEW
        document.safe_error_code = code
        document.safe_error_message = message
        document.processed_at = datetime.now(UTC)
        self._commit()

    def mark_failed(
        self,
        document: CandidateDocument,
        *,
        code: str,
        message: str,
    ) -> None:
        document.profile = None
        document.processing_status = CandidateDocumentProcessingStatus.FAILED
        document.safe_error_code = code
        document.safe_error_message = message
        document.processed_at = datetime.now(UTC)
        self._commit()

    def retrieve_evidence(
        self,
        application_id: UUID,
        query_embedding: list[float],
        *,
        embedding_model: str,
        top_k: int,
    ) -> list[CandidateEvidenceMatch]:
        distance = CandidateCVChunk.embedding.cosine_distance(query_embedding).label("distance")
        statement = (
            select(CandidateCVChunk, distance)
            .join(CandidateDocument, CandidateDocument.id == CandidateCVChunk.document_id)
            .where(
                CandidateDocument.application_id == application_id,
                CandidateDocument.processing_status == CandidateDocumentProcessingStatus.READY,
                CandidateDocument.embedding_model == embedding_model,
                CandidateCVChunk.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(top_k)
        )
        return [
            CandidateEvidenceMatch(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                page_number=chunk.page_number,
                content=chunk.content,
                similarity=1.0 - float(distance_value),
            )
            for chunk, distance_value in self._session.execute(statement).all()
        ]

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise

    def _delete_derived(self, document_id: UUID) -> None:
        self._session.execute(
            delete(CandidateProfile).where(CandidateProfile.source_document_id == document_id)
        )
        self._session.execute(
            delete(CandidateCVChunk).where(CandidateCVChunk.document_id == document_id)
        )
        self._session.flush()
        document = self._session.get(CandidateDocument, document_id)
        if document is not None:
            self._session.expire(document, ["profile", "chunks"])


def task_is_active(task: ProcessingJob | None) -> bool:
    return task is not None and task.status in {
        ProcessingJobStatus.QUEUED,
        ProcessingJobStatus.RUNNING,
    }
