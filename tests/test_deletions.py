from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.applications import (
    Candidate,
    CandidateCVChunk,
    CandidateDocument,
    CandidateDocumentProcessingStatus,
    CandidateProfile,
    JobApplication,
)
from app.models.jobs import Job, RequirementCategory, RequirementType
from app.models.processing_jobs import ProcessingJob, ProcessingJobStatus, ProcessingJobType
from app.models.scoring import CandidateScore, EvidenceCoverage
from app.models.screening import (
    CandidateRequirementEvidence,
    CandidateRequirementMatch,
    CandidateScreening,
    CandidateScreeningStatus,
    RequirementMatchStatus,
)
from tests.test_application_intake import synthetic_pdf


def _create_application(
    client: TestClient,
    job_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    job = client.post("/api/v1/jobs", json=job_payload).json()
    response = client.post(
        f"/api/v1/jobs/{job['id']}/applications/upload",
        files=[
            (
                "files",
                ("synthetic-candidate.pdf", synthetic_pdf(), "application/pdf"),
            )
        ],
    )
    assert response.status_code == 200
    return job, response.json()["files"][0]["application"]


def _count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_delete_application_removes_file_and_all_candidate_derived_data(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
) -> None:
    job, application = _create_application(client, job_payload)
    application_id = UUID(application["id"])
    document_id = UUID(application["document_id"])
    candidate_id = UUID(application["candidate_id"])
    persisted_application = db_session.get(JobApplication, application_id)
    assert persisted_application is not None
    document = db_session.get(CandidateDocument, document_id)
    assert document is not None
    stored_path = tmp_path / "applications" / Path(document.storage_key)
    assert stored_path.is_file()

    chunk = CandidateCVChunk(
        document_id=document_id,
        chunk_index=0,
        page_number=1,
        content="Synthetic Python experience evidence.",
        embedding=[1.0] + [0.0] * 767,
    )
    profile = CandidateProfile(
        candidate_id=candidate_id,
        application_id=application_id,
        source_document_id=document_id,
        structured_json={},
        provider="fake",
        model="fake",
    )
    screening = CandidateScreening(
        job_id=UUID(job["id"]),
        application_id=application_id,
        candidate_document_id=document_id,
        status=CandidateScreeningStatus.COMPLETED,
        requirements_fingerprint="a" * 64,
    )
    db_session.add_all([chunk, profile, screening])
    db_session.flush()
    requirement = db_session.get(Job, UUID(job["id"])).requirements[0]
    match = CandidateRequirementMatch(
        screening_id=screening.id,
        job_requirement_id=requirement.id,
        category=RequirementCategory.SKILL,
        requirement_type=RequirementType.REQUIRED,
        requirement_text=requirement.text,
        priority=1,
        match_status=RequirementMatchStatus.MET,
        justification="Synthetic evidence supports the requirement.",
    )
    db_session.add(match)
    db_session.flush()
    db_session.add_all(
        [
            CandidateRequirementEvidence(
                match_id=match.id,
                candidate_cv_chunk_id=chunk.id,
                retrieval_similarity=0.9,
            ),
            CandidateScore(
                job_id=UUID(job["id"]),
                application_id=application_id,
                screening_id=screening.id,
                overall_score=Decimal("100"),
                dimension_breakdown={},
                evidence_coverage=EvidenceCoverage.HIGH,
                justification="Synthetic deterministic result.",
                scoring_config_version=1,
                scoring_config_fingerprint="b" * 64,
                source_requirement_fingerprint="a" * 64,
                source_screened_at=datetime.now(UTC),
                algorithm_version="F2_V1",
            ),
        ]
    )
    db_session.commit()

    response = client.delete(f"/api/v1/jobs/{job['id']}/applications/{application['id']}")

    assert response.status_code == 204
    db_session.expire_all()
    assert not stored_path.exists()
    assert _count(db_session, JobApplication) == 0
    assert _count(db_session, CandidateDocument) == 0
    assert _count(db_session, CandidateCVChunk) == 0
    assert _count(db_session, CandidateProfile) == 0
    assert _count(db_session, CandidateScreening) == 0
    assert _count(db_session, CandidateRequirementMatch) == 0
    assert _count(db_session, CandidateRequirementEvidence) == 0
    assert _count(db_session, CandidateScore) == 0
    assert _count(db_session, Candidate) == 0
    assert client.get(f"/api/v1/jobs/{job['id']}").status_code == 200
    assert (
        client.delete(f"/api/v1/jobs/{job['id']}/applications/{application['id']}").status_code
        == 404
    )


def test_delete_application_is_blocked_during_active_processing(
    client: TestClient,
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job, application = _create_application(client, job_payload)
    document = db_session.get(CandidateDocument, UUID(application["document_id"]))
    assert document is not None
    task = ProcessingJob(
        job_type=ProcessingJobType.PROCESS_CANDIDATE_DOCUMENT,
        entity_type="CANDIDATE_DOCUMENT",
        entity_id=document.id,
        status=ProcessingJobStatus.RUNNING,
    )
    db_session.add(task)
    db_session.flush()
    document.processing_status = CandidateDocumentProcessingStatus.PROCESSING
    document.processing_task_id = task.id
    db_session.commit()

    response = client.delete(f"/api/v1/jobs/{job['id']}/applications/{application['id']}")

    assert response.status_code == 409
    assert response.json()["code"] == "DELETION_IN_PROGRESS"
    assert client.get(f"/api/v1/jobs/{job['id']}/applications").json()


def test_delete_job_removes_all_local_applications_and_managed_files(
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
    job_payload: dict[str, Any],
) -> None:
    job = client.post("/api/v1/jobs", json=job_payload).json()
    for index in range(2):
        response = client.post(
            f"/api/v1/jobs/{job['id']}/applications/upload",
            files=[
                (
                    "files",
                    (
                        f"candidate-{index}.pdf",
                        synthetic_pdf(f"Candidate {index}"),
                        "application/pdf",
                    ),
                )
            ],
        )
        assert response.json()["imported"] == 1
    assert len(list((tmp_path / "applications").rglob("*.pdf"))) == 2

    response = client.delete(f"/api/v1/jobs/{job['id']}")

    assert response.status_code == 204
    db_session.expire_all()
    assert client.get(f"/api/v1/jobs/{job['id']}").status_code == 404
    assert client.get("/api/v1/jobs").json() == []
    assert _count(db_session, JobApplication) == 0
    assert _count(db_session, CandidateDocument) == 0
    assert _count(db_session, Candidate) == 0
    assert list((tmp_path / "applications").rglob("*.pdf")) == []


def test_delete_controls_are_rendered_for_jobs_and_applications(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    job, _ = _create_application(client, job_payload)

    jobs_page = client.get("/jobs")
    detail_page = client.get(f"/jobs/{job['id']}")
    applications_page = client.get(f"/jobs/{job['id']}/applications")

    assert f'data-delete-job-id="{job["id"]}"' in jobs_page.text
    assert "Delete job" in detail_page.text
    assert "data-delete-application-id" in applications_page.text


def test_delete_missing_job_and_wrong_job_application_are_safe(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    job, application = _create_application(client, job_payload)

    missing_job = client.delete(f"/api/v1/jobs/{uuid4()}")
    wrong_parent = client.delete(f"/api/v1/jobs/{uuid4()}/applications/{application['id']}")

    assert missing_job.status_code == 404
    assert missing_job.json()["code"] == "JOB_NOT_FOUND"
    assert wrong_parent.status_code == 404
    assert wrong_parent.json()["code"] == "APPLICATION_NOT_FOUND"
    assert client.get(f"/api/v1/jobs/{job['id']}/applications").json()
