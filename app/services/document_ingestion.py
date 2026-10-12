from datetime import UTC, datetime
from uuid import UUID

from app.adapters.document_parser import CompanyDocumentParser
from app.adapters.document_storage import LocalDocumentStorage
from app.adapters.embeddings import EmbeddingAdapter
from app.errors import CompanyDocumentNotFoundError, CompanyDocumentValidationError
from app.models.company_documents import (
    CompanyDocumentChunk,
    CompanyDocumentStatus,
)
from app.repositories.company_documents import CompanyDocumentRepository
from app.services.document_chunking import chunk_sections


class DocumentIngestionService:
    def __init__(
        self,
        repository: CompanyDocumentRepository,
        storage: LocalDocumentStorage,
        parser: CompanyDocumentParser,
        embedding_adapter: EmbeddingAdapter,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._parser = parser
        self._embedding_adapter = embedding_adapter

    def ingest(self, document_id: UUID) -> None:
        document = self._repository.get(document_id)
        if document is None:
            raise CompanyDocumentNotFoundError
        document.status = CompanyDocumentStatus.PROCESSING
        document.processing_started_at = datetime.now(UTC)
        document.safe_error_code = None
        document.safe_error_message = None
        self._repository.update(document)

        try:
            sections = self._parser.extract(self._storage.path_for(document.storage_key))
            chunks = chunk_sections(sections)
            if not chunks:
                raise CompanyDocumentValidationError(
                    "The document does not contain enough extractable text."
                )
            embeddings = self._embedding_adapter.embed_documents(
                [chunk.content for chunk in chunks]
            )
            document.chunks = [
                CompanyDocumentChunk(
                    chunk_index=index,
                    content=chunk.content,
                    embedding=embedding,
                    page_number=chunk.page_number,
                    section_title=chunk.section_title,
                )
                for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True))
            ]
            document.embedding_model = self._embedding_adapter.model_name
            document.embedding_dimension = self._embedding_adapter.dimension
            document.chunk_count = len(chunks)
            document.status = CompanyDocumentStatus.READY
            document.processed_at = datetime.now(UTC)
            self._repository.update(document)
        except Exception:
            document.chunks = []
            document.status = CompanyDocumentStatus.FAILED
            document.safe_error_code = "DOCUMENT_INGESTION_FAILED"
            document.safe_error_message = "The company document could not be processed."
            self._repository.update(document)
            raise
