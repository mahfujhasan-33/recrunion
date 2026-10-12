from io import BytesIO
from pathlib import Path

from conftest import FakeEmbeddingAdapter
from sqlalchemy.orm import Session

from app.adapters.document_parser import CompanyDocumentParser
from app.adapters.document_storage import LocalDocumentStorage
from app.models.company_documents import CompanyDocumentStatus, CompanyDocumentType
from app.repositories.company_documents import CompanyDocumentRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.services.company_documents import CompanyDocumentService
from app.services.document_ingestion import DocumentIngestionService


def test_ingestion_parses_chunks_embeds_and_persists(
    db_session: Session,
    fake_embedding_adapter: FakeEmbeddingAdapter,
    tmp_path: Path,
) -> None:
    storage = LocalDocumentStorage(tmp_path, 1024 * 1024)
    parser = CompanyDocumentParser()
    document_repository = CompanyDocumentRepository(db_session)
    upload_service = CompanyDocumentService(
        document_repository,
        ProcessingJobRepository(db_session),
        storage,
        parser,
        worker_max_attempts=3,
    )
    uploaded = upload_service.upload(
        filename="policy.txt",
        content_type="text/plain",
        document_type=CompanyDocumentType.RECRUITMENT_POLICY,
        source=BytesIO(("Recruiters must use evidence-based criteria. " * 120).encode()),
    )

    DocumentIngestionService(
        document_repository,
        storage,
        parser,
        fake_embedding_adapter,
    ).ingest(uploaded.document.id)

    persisted = document_repository.get(uploaded.document.id)
    assert persisted is not None
    assert persisted.status == CompanyDocumentStatus.READY
    assert persisted.embedding_model == "nomic-embed-text"
    assert persisted.embedding_dimension == 768
    assert persisted.chunk_count == len(persisted.chunks)
    assert persisted.chunk_count > 1
    assert len(persisted.chunks[0].embedding) == 768
