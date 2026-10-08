from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.errors import CandidateProcessingError
from app.models.processing_jobs import ProcessingJob, ProcessingJobStatus, ProcessingJobType
from worker import main as worker_main


def worker_settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        ollama_base_url="http://ollama.test",
        embedding_model="nomic-embed-text",
        embedding_dimension=768,
        ollama_timeout_seconds=1,
        gemini_api_key="test-key",
        gemini_model="test-model",
        gemini_timeout_seconds=1,
        applications_root=tmp_path,
        max_cv_size_mb=10,
    )


def test_worker_dispatches_candidate_processing_and_completes_task(
    database_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = sessionmaker(bind=database_engine, expire_on_commit=False)
    task_id = uuid4()
    with factory() as session:
        session.add(
            ProcessingJob(
                id=task_id,
                job_type=ProcessingJobType.PROCESS_CANDIDATE_DOCUMENT,
                entity_type="CANDIDATE_DOCUMENT",
                entity_id=uuid4(),
                max_attempts=1,
            )
        )
        session.commit()

    stages: list[tuple[int, str]] = []

    class FakeCandidateProcessingService:
        async def process(self, document_id, report) -> None:
            assert document_id
            report(20, "Extracting page-aware CV text")
            report(90, "Persisting candidate profile and evidence vectors")
            stages.extend(
                [
                    (20, "Extracting page-aware CV text"),
                    (90, "Persisting candidate profile and evidence vectors"),
                ]
            )

    monkeypatch.setattr(worker_main, "SessionFactory", factory)
    monkeypatch.setattr(worker_main, "get_settings", lambda: worker_settings(tmp_path))
    monkeypatch.setattr(
        worker_main,
        "build_candidate_processing_service",
        lambda *args, **kwargs: FakeCandidateProcessingService(),
    )

    assert worker_main.process_next_task() is True

    with Session(database_engine) as session:
        task = session.get(ProcessingJob, task_id)
        assert task is not None
        assert task.status == ProcessingJobStatus.COMPLETED
        assert task.progress == 100
        assert task.progress_message == "Completed"
    assert stages[-1][0] == 90


def test_worker_records_safe_candidate_processing_failure(
    database_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = sessionmaker(bind=database_engine, expire_on_commit=False)
    task_id = uuid4()
    with factory() as session:
        session.add(
            ProcessingJob(
                id=task_id,
                job_type=ProcessingJobType.PROCESS_CANDIDATE_DOCUMENT,
                entity_type="CANDIDATE_DOCUMENT",
                entity_id=uuid4(),
                max_attempts=1,
            )
        )
        session.commit()

    class FailingCandidateProcessingService:
        async def process(self, document_id, report) -> None:
            raise CandidateProcessingError("The candidate document could not be processed.")

    monkeypatch.setattr(worker_main, "SessionFactory", factory)
    monkeypatch.setattr(worker_main, "get_settings", lambda: worker_settings(tmp_path))
    monkeypatch.setattr(
        worker_main,
        "build_candidate_processing_service",
        lambda *args, **kwargs: FailingCandidateProcessingService(),
    )

    assert worker_main.process_next_task() is True

    with Session(database_engine) as session:
        task = session.get(ProcessingJob, task_id)
        assert task is not None
        assert task.status == ProcessingJobStatus.FAILED
        assert task.error_code == "CANDIDATE_PROCESSING_FAILED"
        assert task.error_message_safe == "The candidate document could not be processed."
