from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.graphs.candidate_screening import build_requirements_fingerprint
from app.models.applications import (
    ApplicationSource,
    ApplicationStatus,
    Candidate,
    CandidateCVChunk,
    CandidateDocument,
    CandidateDocumentProcessingStatus,
    JobApplication,
)
from app.models.jobs import Job, RequirementCategory, RequirementType
from app.models.scoring import CandidateScore, EvidenceCoverage, JobScoringConfig
from app.models.screening import (
    CandidateRequirementEvidence,
    CandidateRequirementMatch,
    CandidateScreening,
    CandidateScreeningStatus,
    RequirementMatchStatus,
)
from app.repositories.candidate_scoring import CandidateScoringRepository
from app.repositories.candidate_screening import CandidateScreeningRepository
from app.repositories.jobs import JobRepository
from app.schemas.jobs import JobWriteRequest
from app.schemas.scoring import ScoringConfigUpdate
from app.services.candidate_scoring import (
    CandidateScoringService,
    calculate_score,
    dimension_for_match,
)
from app.services.jobs import JobService


def create_job(db_session: Session, job_payload: dict[str, Any]) -> Job:
    response = JobService(JobRepository(db_session)).create_job(
        JobWriteRequest.model_validate(job_payload)
    )
    job = JobRepository(db_session).get(response.id)
    assert job is not None
    return job


def create_application(
    db_session: Session,
    job: Job,
    *,
    ready: bool = True,
    evidence_requirement_ids: set[UUID] | None = None,
    statuses: dict[UUID, RequirementMatchStatus] | None = None,
) -> tuple[JobApplication, CandidateScreening]:
    db_session.expire_all()
    persisted_job = JobRepository(db_session).get(job.id)
    assert persisted_job is not None
    job = persisted_job
    candidate = Candidate(id=uuid4(), display_reference=f"Candidate {uuid4().hex[:4]}")
    application = JobApplication(
        id=uuid4(),
        job_id=job.id,
        candidate_id=candidate.id,
        source=ApplicationSource.MANUAL_UPLOAD,
        status=ApplicationStatus.IMPORTED,
        candidate=candidate,
    )
    document = CandidateDocument(
        id=uuid4(),
        application_id=application.id,
        job_id=job.id,
        original_filename="synthetic-candidate.pdf",
        storage_key=f"{job.code}/{uuid4()}.pdf",
        mime_type="application/pdf",
        file_size=128,
        sha256=uuid4().hex + uuid4().hex,
        processing_status=(
            CandidateDocumentProcessingStatus.READY
            if ready
            else CandidateDocumentProcessingStatus.IMPORTED
        ),
        embedding_model="nomic-embed-text" if ready else None,
        embedding_dimension=768 if ready else None,
        chunk_count=1 if ready else 0,
    )
    chunk = CandidateCVChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        page_number=1,
        content="Synthetic evidence demonstrating Python and relevant experience.",
        embedding=[1.0] + [0.0] * 767,
    )
    application.document = document
    if ready:
        document.chunks = [chunk]
    screening = CandidateScreening(
        id=uuid4(),
        job_id=job.id,
        application_id=application.id,
        candidate_document_id=document.id,
        status=CandidateScreeningStatus.COMPLETED,
        requirements_fingerprint=build_requirements_fingerprint(job.requirements),
        screened_at=datetime.now(UTC),
    )
    matches: list[CandidateRequirementMatch] = []
    evidence_requirement_ids = evidence_requirement_ids or set()
    statuses = statuses or {}
    for requirement in job.requirements:
        match = CandidateRequirementMatch(
            id=uuid4(),
            screening_id=screening.id,
            job_requirement_id=requirement.id,
            category=requirement.category,
            requirement_type=requirement.requirement_type,
            requirement_text=requirement.text,
            minimum_value=requirement.minimum_value,
            priority=requirement.priority,
            match_status=statuses.get(requirement.id, RequirementMatchStatus.MET),
            justification="Deterministic persisted M6 outcome.",
        )
        if requirement.id in evidence_requirement_ids:
            match.evidence = [
                CandidateRequirementEvidence(
                    match_id=match.id,
                    candidate_cv_chunk_id=chunk.id,
                    retrieval_similarity=0.91,
                    chunk=chunk,
                )
            ]
        matches.append(match)
    screening.matches = matches
    db_session.add_all([application, screening])
    db_session.commit()
    return application, screening


def build_service(db_session: Session) -> CandidateScoringService:
    return CandidateScoringService(
        JobRepository(db_session),
        CandidateScreeningRepository(db_session),
        CandidateScoringRepository(db_session),
    )


def test_scoring_formula_uses_required_preferred_importance_and_decimal_rounding(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    statuses = {
        requirement.id: (
            RequirementMatchStatus.MET
            if index == 0
            else RequirementMatchStatus.PARTIALLY_MET
            if index == 1
            else RequirementMatchStatus.UNMET
        )
        for index, requirement in enumerate(job.requirements)
    }
    application, screening = create_application(
        db_session,
        job,
        evidence_requirement_ids={job.requirements[0].id},
        statuses=statuses,
    )

    result = build_service(db_session).score_candidate(job.id, application.id)

    assert Decimal("0") <= result.suitability_score <= Decimal("100")
    assert result.suitability_score.as_tuple().exponent == -2
    assert result.algorithm_version == "F2_V1"
    assert result.evidence_coverage == EvidenceCoverage.LOW
    assert len(result.dimensions) == 4
    skill_dimension = next(
        item for item in result.dimensions if item.dimension == "TECHNICAL_SKILLS"
    )
    assert skill_dimension.raw_score == Decimal("60.00")
    assert {item.importance for item in skill_dimension.requirements} == {
        Decimal("1.0"),
        Decimal("0.5"),
    }
    assert "Suitability Score" in result.justification
    assert screening.id == db_session.scalar(select(CandidateScore.screening_id))


def test_na_dimension_is_excluded_and_weights_are_renormalized(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job_payload = {**job_payload, "qualifications": [], "selection_criteria": []}
    job = create_job(db_session, job_payload)
    application, _ = create_application(db_session, job)
    service = build_service(db_session)
    service.update_config(
        job.id,
        ScoringConfigUpdate(
            technical_skills_weight=Decimal("5"),
            relevant_experience_weight=Decimal("3"),
            qualifications_weight=Decimal("2"),
            role_specific_criteria_weight=Decimal("1"),
        ),
    )

    result = service.score_candidate(job.id, application.id)

    qualification = next(
        item
        for item in result.dimensions
        if item.dimension == "ACADEMIC_PROFESSIONAL_QUALIFICATIONS"
    )
    assert qualification.applicable is False
    assert qualification.raw_score is None
    assert qualification.effective_weight is None
    applicable_weight = sum(item.effective_weight or Decimal("0") for item in result.dimensions)
    assert applicable_weight == Decimal("1.000000")
    assert result.suitability_score == Decimal("100.00")


def test_evidence_coverage_is_non_scoring_and_reproducible(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    _, screening = create_application(db_session, job)
    config = JobScoringConfig(
        id=uuid4(),
        job_id=job.id,
        technical_skills_weight=Decimal("1"),
        relevant_experience_weight=Decimal("1"),
        qualifications_weight=Decimal("1"),
        role_specific_criteria_weight=Decimal("1"),
        version=1,
        fingerprint="a" * 64,
    )
    _, without_evidence, coverage_without, _ = calculate_score(screening.matches, config)
    for match in screening.matches:
        match.evidence = [
            CandidateRequirementEvidence(
                match_id=match.id,
                candidate_cv_chunk_id=uuid4(),
                retrieval_similarity=0.5,
            )
        ]
    _, with_evidence, coverage_with, _ = calculate_score(screening.matches, config)

    assert without_evidence == with_evidence
    assert coverage_without == EvidenceCoverage.LOW
    assert coverage_with == EvidenceCoverage.HIGH


def test_config_version_changes_only_for_new_weights_and_stales_score(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    application, _ = create_application(db_session, job)
    service = build_service(db_session)
    original = service.score_candidate(job.id, application.id)
    unchanged = service.update_config(
        job.id,
        ScoringConfigUpdate(
            technical_skills_weight=Decimal("1"),
            relevant_experience_weight=Decimal("1"),
            qualifications_weight=Decimal("1"),
            role_specific_criteria_weight=Decimal("1"),
        ),
    )
    changed = service.update_config(
        job.id,
        ScoringConfigUpdate(
            technical_skills_weight=Decimal("5"),
            relevant_experience_weight=Decimal("3"),
            qualifications_weight=Decimal("2"),
            role_specific_criteria_weight=Decimal("1"),
        ),
    )

    assert unchanged.version == 1
    assert changed.version == 2
    stale = service.get_candidate_score(job.id, application.id)
    assert stale.is_current is False
    assert "weights changed" in (stale.stale_reason or "")
    rescored = service.score_candidate(job.id, application.id)
    assert rescored.score_id == original.score_id
    assert rescored.scoring_config_version == 2
    assert rescored.is_current is True


def test_role_specific_scoring_excludes_subjective_and_protected_criteria() -> None:
    objective = CandidateRequirementMatch(
        id=uuid4(),
        screening_id=uuid4(),
        job_requirement_id=uuid4(),
        category=RequirementCategory.SELECTION_CRITERION,
        requirement_type=RequirementType.REQUIRED,
        requirement_text="Evidence of production API ownership",
        priority=1,
        match_status=RequirementMatchStatus.MET,
        justification="Persisted M6 outcome.",
    )
    subjective = CandidateRequirementMatch(
        id=uuid4(),
        screening_id=uuid4(),
        job_requirement_id=uuid4(),
        category=RequirementCategory.SELECTION_CRITERION,
        requirement_type=RequirementType.REQUIRED,
        requirement_text="Strong confidence and likability",
        priority=2,
        match_status=RequirementMatchStatus.MET,
        justification="Persisted M6 outcome.",
    )
    protected = CandidateRequirementMatch(
        id=uuid4(),
        screening_id=uuid4(),
        job_requirement_id=uuid4(),
        category=RequirementCategory.SELECTION_CRITERION,
        requirement_type=RequirementType.REQUIRED,
        requirement_text="Preferred nationality",
        priority=3,
        match_status=RequirementMatchStatus.MET,
        justification="Persisted M6 outcome.",
    )

    assert dimension_for_match(objective)[0] == "ROLE_SPECIFIC_CRITERIA"
    assert dimension_for_match(subjective)[0] is None
    assert "Subjective" in (dimension_for_match(subjective)[1] or "")
    assert dimension_for_match(protected)[0] is None
    assert "Protected" in (dimension_for_match(protected)[1] or "")


def test_replaced_screening_timestamp_marks_existing_score_stale(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    application, screening = create_application(db_session, job)
    service = build_service(db_session)
    service.score_candidate(job.id, application.id)
    screening.screened_at = datetime.now(UTC).replace(microsecond=1)
    db_session.add(screening)
    db_session.commit()

    stale = service.get_candidate_score(job.id, application.id)

    assert stale.is_current is False
    assert "screened again" in (stale.stale_reason or "")


def test_batch_scores_eligible_candidates_independently_and_skips_ineligible(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    create_application(db_session, job)
    create_application(db_session, job, ready=False)

    result = build_service(db_session).score_screened(job.id)

    assert result.eligible == 1
    assert result.scored == 1
    assert result.skipped == 1
    assert result.failed == 0
    assert db_session.scalar(select(func.count()).select_from(CandidateScore)) == 1


def test_scoring_api_rejects_unprocessed_candidate_and_validates_weights(
    client: TestClient,
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    application, _ = create_application(db_session, job, ready=False)

    scoring = client.post(f"/api/v1/jobs/{job.id}/applications/{application.id}/score")
    invalid_config = client.put(
        f"/api/v1/jobs/{job.id}/scoring-config",
        json={
            "technical_skills_weight": 0,
            "relevant_experience_weight": 1,
            "qualifications_weight": 1,
            "role_specific_criteria_weight": 1,
        },
    )

    assert scoring.status_code == 409
    assert scoring.json()["code"] == "CANDIDATE_SCORING_STATE_INVALID"
    assert invalid_config.status_code == 422


def test_scoring_api_persists_retrieves_and_batches_without_duplicate_scores(
    client: TestClient,
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)
    application, _ = create_application(
        db_session,
        job,
        evidence_requirement_ids={item.id for item in job.requirements},
    )

    created = client.post(f"/api/v1/jobs/{job.id}/applications/{application.id}/score")
    repeated = client.post(f"/api/v1/jobs/{job.id}/applications/{application.id}/score")
    batch = client.post(f"/api/v1/jobs/{job.id}/applications/score-screened")
    listing = client.get(f"/api/v1/jobs/{job.id}/scores")
    detail = client.get(f"/api/v1/jobs/{job.id}/applications/{application.id}/score")

    assert created.status_code == 200, created.text
    assert repeated.json()["score_id"] == created.json()["score_id"]
    assert batch.json()["already_current"] == 1
    assert listing.json()["candidates"][0]["suitability_score"] == "100.00"
    assert detail.json()["evidence_coverage"] == "HIGH"
    assert db_session.scalar(select(func.count()).select_from(CandidateScore)) == 1


def test_applications_ui_distinguishes_match_rank_and_suitability_score(
    client: TestClient,
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job = create_job(db_session, job_payload)

    response = client.get(f"/jobs/{job.id}/applications")

    assert response.status_code == 200
    assert "Match Rank" in response.text
    assert "Suitability Score" in response.text
    assert "Score eligible candidates" in response.text
