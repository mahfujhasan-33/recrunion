import asyncio
import logging
import tempfile
import time
from pathlib import Path

from app.adapters.bluesky import BlueskyPublisher
from app.adapters.document_parser import CompanyDocumentParser
from app.adapters.document_storage import LocalDocumentStorage
from app.adapters.gemini import GeminiLLMAdapter
from app.adapters.ollama_embeddings import OllamaEmbeddingAdapter
from app.config import get_settings
from app.database import SessionFactory
from app.dependencies import build_assistant_service, build_job_publishing_service
from app.errors import CompanyDocumentValidationError, RecrUnionError
from app.models.processing_jobs import ProcessingJobType
from app.repositories.company_documents import CompanyDocumentRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.services.document_ingestion import DocumentIngestionService

HEARTBEAT_PATH = Path(tempfile.gettempdir()) / "recrunion-worker-heartbeat"
logger = logging.getLogger(__name__)


def run_worker() -> None:
    """Claim and execute PostgreSQL-backed background jobs."""

    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("Worker started", extra={"operation": "worker_start", "status": "running"})

    while True:
        HEARTBEAT_PATH.touch()
        processed = process_next_task()
        if not processed:
            time.sleep(settings.worker_poll_seconds)


def process_next_task() -> bool:
    settings = get_settings()
    with SessionFactory() as session:
        task_repository = ProcessingJobRepository(session)
        task = task_repository.claim_next()
        if task is None:
            return False
        try:
            embedding_adapter = OllamaEmbeddingAdapter(
                base_url=settings.ollama_base_url,
                model=settings.embedding_model,
                dimension=settings.embedding_dimension,
                timeout_seconds=settings.ollama_timeout_seconds,
            )
            if task.job_type == ProcessingJobType.COMPANY_DOCUMENT_INGESTION:
                ingestion = DocumentIngestionService(
                    CompanyDocumentRepository(session),
                    LocalDocumentStorage(
                        settings.company_documents_root,
                        settings.max_company_document_size_mb * 1024 * 1024,
                    ),
                    CompanyDocumentParser(),
                    embedding_adapter,
                )
                task_repository.update_progress(task, 10, "Extracting document text")
                ingestion.ingest(task.entity_id)
            elif task.job_type == ProcessingJobType.ASSISTANT_TURN:
                assistant = build_assistant_service(
                    session,
                    GeminiLLMAdapter(
                        api_key=settings.gemini_api_key,
                        model=settings.gemini_model,
                        timeout_seconds=settings.gemini_timeout_seconds,
                    ),
                    embedding_adapter,
                )
                asyncio.run(
                    assistant.process_turn(
                        task.entity_id,
                        lambda progress, message: task_repository.update_progress(
                            task, progress, message
                        ),
                    )
                )
            elif task.job_type == ProcessingJobType.JOB_PUBLICATION:
                publishing = build_job_publishing_service(
                    session,
                    BlueskyPublisher(
                        identifier=settings.bluesky_identifier,
                        app_password=settings.bluesky_app_password,
                        service_url=settings.bluesky_service_url,
                        timeout_seconds=settings.bluesky_timeout_seconds,
                    ),
                )
                asyncio.run(
                    publishing.process(
                        task.entity_id,
                        lambda progress, message: task_repository.update_progress(
                            task, progress, message
                        ),
                    )
                )
            else:
                raise CompanyDocumentValidationError("Unsupported background job type.")
            task_repository.complete(task)
        except Exception as error:
            if isinstance(error, CompanyDocumentValidationError):
                task.max_attempts = task.attempt_count
            code = error.code if isinstance(error, RecrUnionError) else "BACKGROUND_TASK_FAILED"
            safe_message = (
                str(error)
                if isinstance(error, RecrUnionError)
                else "The background task could not be completed."
            )
            task_repository.fail_or_retry(
                task,
                code,
                safe_message,
            )
            logger.warning(
                "Background task failed",
                extra={
                    "task_id": str(task.id),
                    "entity_id": str(task.entity_id),
                    "error_code": code,
                },
            )
        return True


if __name__ == "__main__":
    run_worker()
