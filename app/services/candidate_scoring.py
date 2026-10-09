import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from app.errors import (
    ApplicationNotFoundError,
    CandidateScoringError,
    CandidateScoringNotFoundError,
    CandidateScoringStateError,
    JobNotFoundError,
    ScoringConfigurationError,
)
from app.graphs.candidate_screening import (
    build_requirements_fingerprint,
    contains_protected_screening_term,
)
from app.models.applications import CandidateDocumentProcessingStatus, JobApplication
from app.models.jobs import Job, JobRequirement, RequirementCategory, RequirementType
from app.models.scoring import (
    CandidateScore,
    EvidenceCoverage,
    JobScoringConfig,
    ScoringDimension,
)
from app.models.screening import (
    CandidateRequirementMatch,
    CandidateScreening,
    CandidateScreeningStatus,
    RequirementMatchStatus,
)
from app.repositories.candidate_scoring import CandidateScoringRepository
from app.repositories.candidate_screening import CandidateScreeningRepository
from app.repositories.jobs import JobRepository
from app.schemas.scoring import (
    CandidateScoreListEntry,
    CandidateScoreResponse,
    CandidateScoreSnapshot,
    DimensionBreakdownResponse,
    DimensionBreakdownSnapshot,
    DimensionScoreSummary,
    ExcludedScoringRequirement,
    JobScoresResponse,
    ScoreBatchResponse,
    ScoreRequirementResponse,
    ScoreRequirementSnapshot,
    ScoringConfigDimension,
    ScoringConfigResponse,
    ScoringConfigUpdate,
)
from app.schemas.screening import ScreeningEvidenceResponse
from app.services.candidate_screening import ranking_key

ALGORITHM_VERSION = "F2_V1"
SCORE_QUANTUM = Decimal("0.01")
WEIGHT_QUANTUM = Decimal("0.000001")
MATCH_POINTS = {
    RequirementMatchStatus.MET: Decimal("100"),
    RequirementMatchStatus.PARTIALLY_MET: Decimal("50"),
    RequirementMatchStatus.UNMET: Decimal("0"),
}
REQUIREMENT_IMPORTANCE = {
    RequirementType.REQUIRED: Decimal("1.0"),
    RequirementType.PREFERRED: Decimal("0.5"),
}
DIMENSION_ORDER = (
    ScoringDimension.TECHNICAL_SKILLS,
    ScoringDimension.RELEVANT_EXPERIENCE,
    ScoringDimension.ACADEMIC_PROFESSIONAL_QUALIFICATIONS,
    ScoringDimension.ROLE_SPECIFIC_CRITERIA,
)
DIMENSION_LABELS = {
    ScoringDimension.TECHNICAL_SKILLS: "Technical Skills",
    ScoringDimension.RELEVANT_EXPERIENCE: "Relevant Experience",
    ScoringDimension.ACADEMIC_PROFESSIONAL_QUALIFICATIONS: (
        "Academic / Professional Qualifications"
    ),
    ScoringDimension.ROLE_SPECIFIC_CRITERIA: "Role-Specific Criteria",
}
NON_SCORABLE_SELECTION_PHRASES = (
    "attitude",
    "confidence",
    "culture fit",
    "cultural fit",
    "disciplined",
    "emotional intelligence",
    "likability",
    "likeability",
    "personality",
    "teamwork mindset",
    "well behaved",
    "well-behaved",
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoreOperationResult:
    score: CandidateScore
    outcome: str


class CandidateScoringService:
    """Calculate reproducible F2 scores from validated persisted F1 outcomes."""

    def __init__(
        self,
        job_repository: JobRepository,
        screening_repository: CandidateScreeningRepository,
        scoring_repository: CandidateScoringRepository,
    ) -> None:
        self._job_repository = job_repository
        self._screening_repository = screening_repository
        self._scoring_repository = scoring_repository

    def get_config(self, job_id: UUID) -> ScoringConfigResponse:
        job = self._get_job(job_id)
        config = self._get_or_create_config(job.id)
        return config_response(job, config)

    def update_config(
        self,
        job_id: UUID,
        payload: ScoringConfigUpdate,
    ) -> ScoringConfigResponse:
        job = self._get_job(job_id)
        weights = weights_from_payload(payload)
        fingerprint = build_config_fingerprint(weights)
        config = self._scoring_repository.get_config(job_id, lock=True)
        if config is None:
            config = JobScoringConfig(
                id=uuid4(),
                job_id=job_id,
                version=1,
                fingerprint=fingerprint,
                **config_weight_fields(weights),
            )
            config = self._scoring_repository.save_config(config)
        elif config.fingerprint != fingerprint:
            apply_weights(config, weights)
            config.version += 1
            config.fingerprint = fingerprint
            config = self._scoring_repository.save_config(config)
        return config_response(job, config)

    def score_candidate(self, job_id: UUID, application_id: UUID) -> CandidateScoreResponse:
        job = self._get_job(job_id)
        application = self._screening_repository.lock_application(job_id, application_id)
        if application is None:
            raise ApplicationNotFoundError
        config = self._get_or_create_config(job_id)
        result = self._score(job, application, config)
        return self._to_detail(job, application, result.score, config)

    def score_screened(self, job_id: UUID) -> ScoreBatchResponse:
        job = self._get_job(job_id)
        config = self._get_or_create_config(job_id)
        eligible = 0
        scored = 0
        already_current = 0
        stale_replaced = 0
        skipped = 0
        failed = 0
        for application in self._screening_repository.list_applications(job_id):
            try:
                screening = self._screening_repository.get_for_application(application.id)
                validate_score_eligibility(job, application, screening)
                eligible += 1
                result = self._score(job, application, config, screening=screening)
                if result.outcome == "CURRENT":
                    already_current += 1
                elif result.outcome == "REPLACED":
                    stale_replaced += 1
                else:
                    scored += 1
            except CandidateScoringStateError:
                skipped += 1
            except Exception:
                self._scoring_repository.rollback()
                failed += 1
                logger.exception(
                    "Candidate scoring failed",
                    extra={
                        "application_id": str(application.id),
                        "operation": "candidate_scoring",
                        "error_code": CandidateScoringError.code,
                    },
                )
        return ScoreBatchResponse(
            eligible=eligible,
            scored=scored,
            already_current=already_current,
            stale_replaced=stale_replaced,
            skipped=skipped,
            failed=failed,
        )

    def get_candidate_score(
        self,
        job_id: UUID,
        application_id: UUID,
    ) -> CandidateScoreResponse:
        job = self._get_job(job_id)
        applications = {
            item.id: item for item in self._screening_repository.list_applications(job_id)
        }
        application = applications.get(application_id)
        if application is None:
            raise ApplicationNotFoundError
        score = self._scoring_repository.get_score(application_id)
        if score is None:
            raise CandidateScoringNotFoundError
        return self._to_detail(job, application, score, self._get_or_create_config(job_id))

    def get_job_scores(self, job_id: UUID) -> JobScoresResponse:
        job = self._get_job(job_id)
        config = self._get_or_create_config(job_id)
        scores = {
            item.application_id: item for item in self._scoring_repository.list_scores(job_id)
        }
        screenings = {
            item.application_id: item for item in self._screening_repository.list_for_job(job_id)
        }
        ranks = build_rank_map(job, list(screenings.values()))
        candidates: list[CandidateScoreListEntry] = []
        for application in self._screening_repository.list_applications(job_id):
            score = scores.get(application.id)
            screening = screenings.get(application.id)
            if score is None:
                candidates.append(
                    CandidateScoreListEntry(
                        application_id=application.id,
                        candidate_reference=application.candidate.display_reference,
                        match_rank=ranks.get(application.id),
                        suitability_score=None,
                        required_gaps=(
                            screening.required_unmet
                            if screening is not None and application.id in ranks
                            else None
                        ),
                        evidence_coverage=None,
                        is_current=False,
                        stale_reason="Not scored — no suitability score is available.",
                        dimensions=[],
                    )
                )
                continue
            snapshot = CandidateScoreSnapshot.model_validate(score.dimension_breakdown)
            stale_reason = score_stale_reason(job, screening, config, score)
            candidates.append(
                CandidateScoreListEntry(
                    application_id=application.id,
                    candidate_reference=application.candidate.display_reference,
                    match_rank=ranks.get(application.id),
                    suitability_score=score.overall_score,
                    required_gaps=required_gap_count(snapshot),
                    evidence_coverage=score.evidence_coverage,
                    is_current=stale_reason is None,
                    stale_reason=stale_reason,
                    dimensions=[
                        DimensionScoreSummary(
                            dimension=item.dimension,
                            applicable=item.applicable,
                            raw_score=item.raw_score,
                        )
                        for item in snapshot.dimensions
                    ],
                )
            )
        candidates.sort(
            key=lambda item: (
                item.match_rank is None,
                item.match_rank or 0,
                item.candidate_reference.casefold(),
                item.application_id.hex,
            )
        )
        return JobScoresResponse(job_id=job_id, candidates=candidates)

    def _score(
        self,
        job: Job,
        application: JobApplication,
        config: JobScoringConfig,
        *,
        screening: CandidateScreening | None = None,
    ) -> ScoreOperationResult:
        screening = screening or self._screening_repository.get_for_application(application.id)
        validate_score_eligibility(job, application, screening)
        assert screening is not None and screening.screened_at is not None
        existing = self._scoring_repository.get_score(application.id, lock=True)
        if existing is not None and score_stale_reason(job, screening, config, existing) is None:
            return ScoreOperationResult(score=existing, outcome="CURRENT")
        snapshot, overall_score, coverage, justification = calculate_score(
            screening.matches, config
        )
        now = datetime.now(UTC)
        if existing is None:
            score = CandidateScore(
                id=uuid4(),
                job_id=job.id,
                application_id=application.id,
                screening_id=screening.id,
                calculated_at=now,
            )
            outcome = "CREATED"
        else:
            score = existing
            outcome = "REPLACED"
        score.screening_id = screening.id
        score.overall_score = overall_score
        score.dimension_breakdown = snapshot.model_dump(mode="json")
        score.evidence_coverage = coverage
        score.justification = justification
        score.scoring_config_version = config.version
        score.scoring_config_fingerprint = config.fingerprint
        score.source_requirement_fingerprint = screening.requirements_fingerprint
        score.source_screened_at = screening.screened_at
        score.algorithm_version = ALGORITHM_VERSION
        score.calculated_at = now
        return ScoreOperationResult(
            score=self._scoring_repository.save_score(score), outcome=outcome
        )

    def _to_detail(
        self,
        job: Job,
        application: JobApplication,
        score: CandidateScore,
        config: JobScoringConfig,
    ) -> CandidateScoreResponse:
        screening = self._screening_repository.get_for_application(application.id)
        snapshot = CandidateScoreSnapshot.model_validate(score.dimension_breakdown)
        matches = {item.id: item for item in screening.matches} if screening else {}
        dimensions = [enrich_dimension(item, matches) for item in snapshot.dimensions]
        ranks = build_rank_map(job, self._screening_repository.list_for_job(job.id))
        stale_reason = score_stale_reason(job, screening, config, score)
        return CandidateScoreResponse(
            score_id=score.id,
            job_id=score.job_id,
            application_id=score.application_id,
            candidate_reference=application.candidate.display_reference,
            match_rank=ranks.get(application.id),
            suitability_score=score.overall_score,
            required_gaps=required_gap_count(snapshot),
            evidence_coverage=score.evidence_coverage,
            is_current=stale_reason is None,
            stale_reason=stale_reason,
            dimensions=dimensions,
            excluded_requirements=snapshot.excluded_requirements,
            justification=score.justification,
            scoring_config_version=score.scoring_config_version,
            algorithm_version=score.algorithm_version,
            calculated_at=score.calculated_at,
        )

    def _get_or_create_config(self, job_id: UUID) -> JobScoringConfig:
        config = self._scoring_repository.get_config(job_id)
        if config is not None:
            return config
        weights = default_weights()
        return self._scoring_repository.save_config(
            JobScoringConfig(
                id=uuid4(),
                job_id=job_id,
                version=1,
                fingerprint=build_config_fingerprint(weights),
                **config_weight_fields(weights),
            )
        )

    def _get_job(self, job_id: UUID) -> Job:
        job = self._job_repository.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job


def calculate_score(
    matches: list[CandidateRequirementMatch],
    config: JobScoringConfig,
) -> tuple[CandidateScoreSnapshot, Decimal, EvidenceCoverage, str]:
    grouped = {dimension: [] for dimension in DIMENSION_ORDER}
    excluded: list[ExcludedScoringRequirement] = []
    for match in sorted(matches, key=lambda item: (item.priority, item.id.hex)):
        dimension, exclusion_reason = dimension_for_match(match)
        if dimension is None:
            excluded.append(
                ExcludedScoringRequirement(
                    match_id=match.id,
                    requirement_id=match.job_requirement_id,
                    requirement_text=match.requirement_text,
                    reason=exclusion_reason or "This criterion is not part of F2_V1.",
                )
            )
            continue
        grouped[dimension].append(match)
    applicable = [dimension for dimension in DIMENSION_ORDER if grouped[dimension]]
    if not applicable:
        raise ScoringConfigurationError("No applicable requirements are available for scoring.")
    weights = weights_from_config(config)
    applicable_weight_total = sum((weights[item] for item in applicable), Decimal("0"))
    if applicable_weight_total <= 0:
        raise ScoringConfigurationError("Applicable scoring weights must total more than zero.")
    dimensions: list[DimensionBreakdownSnapshot] = []
    contributions: list[Decimal] = []
    for dimension in DIMENSION_ORDER:
        dimension_matches = grouped[dimension]
        configured_weight = weights[dimension]
        if not dimension_matches:
            dimensions.append(
                DimensionBreakdownSnapshot(
                    dimension=dimension,
                    applicable=False,
                    raw_score=None,
                    configured_weight=configured_weight,
                    effective_weight=None,
                    weighted_contribution=None,
                    justification=(
                        f"Not applicable because this job has no scorable "
                        f"{DIMENSION_LABELS[dimension].lower()} requirements."
                    ),
                    requirements=[],
                )
            )
            continue
        requirement_snapshots = [requirement_snapshot(item) for item in dimension_matches]
        importance_total = sum(
            (REQUIREMENT_IMPORTANCE[item.requirement_type] for item in dimension_matches),
            Decimal("0"),
        )
        weighted_points = sum(
            (
                MATCH_POINTS[item.match_status] * REQUIREMENT_IMPORTANCE[item.requirement_type]
                for item in dimension_matches
            ),
            Decimal("0"),
        )
        raw_score_exact = weighted_points / importance_total
        effective_weight_exact = configured_weight / applicable_weight_total
        contribution_exact = raw_score_exact * effective_weight_exact
        contributions.append(contribution_exact)
        dimensions.append(
            DimensionBreakdownSnapshot(
                dimension=dimension,
                applicable=True,
                raw_score=quantize_score(raw_score_exact),
                configured_weight=configured_weight,
                effective_weight=effective_weight_exact.quantize(
                    WEIGHT_QUANTUM, rounding=ROUND_HALF_UP
                ),
                weighted_contribution=quantize_score(contribution_exact),
                justification=dimension_justification(dimension, dimension_matches),
                requirements=requirement_snapshots,
            )
        )
    overall_score = quantize_score(sum(contributions, Decimal("0")))
    included_matches = [item for dimension in applicable for item in grouped[dimension]]
    evidence_count = sum(1 for item in included_matches if item.evidence)
    coverage = evidence_coverage(evidence_count, len(included_matches))
    contribution_text = ", ".join(
        f"{DIMENSION_LABELS[item.dimension]} {item.weighted_contribution}"
        for item in dimensions
        if item.applicable
    )
    justification = (
        f"Suitability Score: {overall_score}/100 using scoring configuration version "
        f"{config.version}. Contributions: {contribution_text}. Evidence Coverage is "
        f"{coverage.value} ({evidence_count} of {len(included_matches)} contributing "
        "requirements reference persisted CV evidence)."
    )
    return (
        CandidateScoreSnapshot(dimensions=dimensions, excluded_requirements=excluded),
        overall_score,
        coverage,
        justification,
    )


def dimension_for_match(
    match: CandidateRequirementMatch,
) -> tuple[ScoringDimension | None, str | None]:
    mapping = {
        RequirementCategory.SKILL: ScoringDimension.TECHNICAL_SKILLS,
        RequirementCategory.EXPERIENCE: ScoringDimension.RELEVANT_EXPERIENCE,
        RequirementCategory.QUALIFICATION: (ScoringDimension.ACADEMIC_PROFESSIONAL_QUALIFICATIONS),
    }
    if match.category in mapping:
        return mapping[match.category], None
    if match.category != RequirementCategory.SELECTION_CRITERION:
        return None, "The requirement category is not supported by F2_V1."
    normalized = match.requirement_text.casefold()
    if contains_protected_screening_term(match.requirement_text):
        return None, "Protected-characteristic criteria are excluded from scoring."
    if any(phrase in normalized for phrase in NON_SCORABLE_SELECTION_PHRASES):
        return None, "Subjective personal-trait criteria are excluded from scoring."
    return ScoringDimension.ROLE_SPECIFIC_CRITERIA, None


def requirement_snapshot(match: CandidateRequirementMatch) -> ScoreRequirementSnapshot:
    return ScoreRequirementSnapshot(
        match_id=match.id,
        requirement_id=match.job_requirement_id,
        requirement_text=match.requirement_text,
        requirement_type=match.requirement_type,
        match_status=match.match_status,
        points=MATCH_POINTS[match.match_status],
        importance=REQUIREMENT_IMPORTANCE[match.requirement_type],
    )


def dimension_justification(
    dimension: ScoringDimension,
    matches: list[CandidateRequirementMatch],
) -> str:
    required = [item for item in matches if item.requirement_type == RequirementType.REQUIRED]
    preferred = [item for item in matches if item.requirement_type == RequirementType.PREFERRED]
    parts = [outcome_summary(required, "required")]
    if preferred:
        parts.append(outcome_summary(preferred, "preferred"))
    partial = [
        item.requirement_text
        for item in matches
        if item.match_status == RequirementMatchStatus.PARTIALLY_MET
    ]
    unmet = [
        item.requirement_text
        for item in matches
        if item.match_status == RequirementMatchStatus.UNMET
    ]
    if partial:
        parts.append(f"Partially demonstrated: {', '.join(partial)}.")
    if unmet:
        parts.append(f"Not sufficiently demonstrated by the submitted CV: {', '.join(unmet)}.")
    if not partial and not unmet:
        parts.append(
            f"All scored {DIMENSION_LABELS[dimension].lower()} requirement outcomes are MET."
        )
    return " ".join(parts)


def outcome_summary(matches: list[CandidateRequirementMatch], label: str) -> str:
    counts = {
        status: sum(1 for item in matches if item.match_status == status)
        for status in RequirementMatchStatus
    }
    return (
        f"{len(matches)} {label} requirements: {counts[RequirementMatchStatus.MET]} met, "
        f"{counts[RequirementMatchStatus.PARTIALLY_MET]} partially met, and "
        f"{counts[RequirementMatchStatus.UNMET]} unmet."
    )


def evidence_coverage(evidence_count: int, requirement_count: int) -> EvidenceCoverage:
    ratio = Decimal(evidence_count) / Decimal(requirement_count)
    if ratio >= Decimal("0.80"):
        return EvidenceCoverage.HIGH
    if ratio >= Decimal("0.50"):
        return EvidenceCoverage.MEDIUM
    return EvidenceCoverage.LOW


def validate_score_eligibility(
    job: Job,
    application: JobApplication,
    screening: CandidateScreening | None,
) -> None:
    if (
        application.document is None
        or application.document.processing_status != CandidateDocumentProcessingStatus.READY
    ):
        raise CandidateScoringStateError("Suitability scoring requires a READY processed CV.")
    if screening is None or screening.status != CandidateScreeningStatus.COMPLETED:
        raise CandidateScoringStateError(
            "Suitability scoring requires a completed candidate screening."
        )
    fingerprint = build_requirements_fingerprint(job.requirements)
    if screening.requirements_fingerprint != fingerprint:
        raise CandidateScoringStateError(
            "Candidate screening is stale because the job requirements changed."
        )
    current_ids = {item.id for item in job.requirements}
    match_ids = {item.job_requirement_id for item in screening.matches}
    if current_ids != match_ids or len(screening.matches) != len(current_ids):
        raise CandidateScoringStateError(
            "Candidate screening does not contain every current job requirement."
        )
    if screening.screened_at is None:
        raise CandidateScoringStateError("Candidate screening has no completion timestamp.")


def score_stale_reason(
    job: Job,
    screening: CandidateScreening | None,
    config: JobScoringConfig,
    score: CandidateScore,
) -> str | None:
    if screening is None or screening.status != CandidateScreeningStatus.COMPLETED:
        return "Stale — the source screening is not completed."
    current_fingerprint = build_requirements_fingerprint(job.requirements)
    if screening.requirements_fingerprint != current_fingerprint:
        return "Stale — job requirements changed after screening."
    if score.source_requirement_fingerprint != current_fingerprint:
        return "Stale — the score uses an earlier requirement set."
    if score.screening_id != screening.id or not same_timestamp(
        score.source_screened_at, screening.screened_at
    ):
        return "Stale — the candidate was screened again."
    if score.scoring_config_fingerprint != config.fingerprint:
        return "Stale — scoring weights changed."
    if score.algorithm_version != ALGORITHM_VERSION:
        return "Stale — the scoring algorithm changed."
    return None


def build_rank_map(job: Job, screenings: list[CandidateScreening]) -> dict[UUID, int]:
    fingerprint = build_requirements_fingerprint(job.requirements)
    current = [
        item
        for item in screenings
        if item.status == CandidateScreeningStatus.COMPLETED
        and item.requirements_fingerprint == fingerprint
    ]
    current.sort(key=ranking_key)
    return {item.application_id: index for index, item in enumerate(current, start=1)}


def config_response(job: Job, config: JobScoringConfig) -> ScoringConfigResponse:
    weights = weights_from_config(config)
    applicable = applicable_dimensions(job.requirements)
    total = sum((weights[item] for item in applicable), Decimal("0"))
    if total <= 0:
        raise ScoringConfigurationError("Applicable scoring weights must total more than zero.")
    return ScoringConfigResponse(
        job_id=job.id,
        version=config.version,
        dimensions=[
            ScoringConfigDimension(
                dimension=dimension,
                configured_weight=weights[dimension],
                applicable=dimension in applicable,
                effective_weight=(
                    (weights[dimension] / total).quantize(WEIGHT_QUANTUM, rounding=ROUND_HALF_UP)
                    if dimension in applicable
                    else None
                ),
            )
            for dimension in DIMENSION_ORDER
        ],
        updated_at=config.updated_at,
    )


def applicable_dimensions(requirements: list[JobRequirement]) -> set[ScoringDimension]:
    dimensions: set[ScoringDimension] = set()
    for requirement in requirements:
        if requirement.category == RequirementCategory.SKILL:
            dimensions.add(ScoringDimension.TECHNICAL_SKILLS)
        elif requirement.category == RequirementCategory.EXPERIENCE:
            dimensions.add(ScoringDimension.RELEVANT_EXPERIENCE)
        elif requirement.category == RequirementCategory.QUALIFICATION:
            dimensions.add(ScoringDimension.ACADEMIC_PROFESSIONAL_QUALIFICATIONS)
        elif requirement.category == RequirementCategory.SELECTION_CRITERION:
            normalized = requirement.text.casefold()
            if not contains_protected_screening_term(requirement.text) and not any(
                phrase in normalized for phrase in NON_SCORABLE_SELECTION_PHRASES
            ):
                dimensions.add(ScoringDimension.ROLE_SPECIFIC_CRITERIA)
    return dimensions


def enrich_dimension(
    snapshot: DimensionBreakdownSnapshot,
    matches: dict[UUID, CandidateRequirementMatch],
) -> DimensionBreakdownResponse:
    requirements: list[ScoreRequirementResponse] = []
    for requirement in snapshot.requirements:
        match = matches.get(requirement.match_id)
        evidence = (
            [
                ScreeningEvidenceResponse(
                    chunk_id=item.candidate_cv_chunk_id,
                    page_number=item.chunk.page_number,
                    excerpt=item.chunk.content[:700],
                    similarity=item.retrieval_similarity,
                )
                for item in match.evidence
            ]
            if match is not None
            else []
        )
        requirements.append(ScoreRequirementResponse(**requirement.model_dump(), evidence=evidence))
    return DimensionBreakdownResponse(
        dimension=snapshot.dimension,
        applicable=snapshot.applicable,
        raw_score=snapshot.raw_score,
        configured_weight=snapshot.configured_weight,
        effective_weight=snapshot.effective_weight,
        weighted_contribution=snapshot.weighted_contribution,
        justification=snapshot.justification,
        requirements=requirements,
    )


def required_gap_count(snapshot: CandidateScoreSnapshot) -> int:
    return sum(
        1
        for dimension in snapshot.dimensions
        for requirement in dimension.requirements
        if requirement.requirement_type == RequirementType.REQUIRED
        and requirement.match_status == RequirementMatchStatus.UNMET
    )


def default_weights() -> dict[ScoringDimension, Decimal]:
    return {dimension: Decimal("1") for dimension in DIMENSION_ORDER}


def weights_from_payload(payload: ScoringConfigUpdate) -> dict[ScoringDimension, Decimal]:
    return {
        ScoringDimension.TECHNICAL_SKILLS: payload.technical_skills_weight,
        ScoringDimension.RELEVANT_EXPERIENCE: payload.relevant_experience_weight,
        ScoringDimension.ACADEMIC_PROFESSIONAL_QUALIFICATIONS: payload.qualifications_weight,
        ScoringDimension.ROLE_SPECIFIC_CRITERIA: payload.role_specific_criteria_weight,
    }


def weights_from_config(config: JobScoringConfig) -> dict[ScoringDimension, Decimal]:
    return {
        ScoringDimension.TECHNICAL_SKILLS: config.technical_skills_weight,
        ScoringDimension.RELEVANT_EXPERIENCE: config.relevant_experience_weight,
        ScoringDimension.ACADEMIC_PROFESSIONAL_QUALIFICATIONS: config.qualifications_weight,
        ScoringDimension.ROLE_SPECIFIC_CRITERIA: config.role_specific_criteria_weight,
    }


def config_weight_fields(weights: dict[ScoringDimension, Decimal]) -> dict[str, Decimal]:
    return {
        "technical_skills_weight": weights[ScoringDimension.TECHNICAL_SKILLS],
        "relevant_experience_weight": weights[ScoringDimension.RELEVANT_EXPERIENCE],
        "qualifications_weight": weights[ScoringDimension.ACADEMIC_PROFESSIONAL_QUALIFICATIONS],
        "role_specific_criteria_weight": weights[ScoringDimension.ROLE_SPECIFIC_CRITERIA],
    }


def apply_weights(
    config: JobScoringConfig,
    weights: dict[ScoringDimension, Decimal],
) -> None:
    config.technical_skills_weight = weights[ScoringDimension.TECHNICAL_SKILLS]
    config.relevant_experience_weight = weights[ScoringDimension.RELEVANT_EXPERIENCE]
    config.qualifications_weight = weights[ScoringDimension.ACADEMIC_PROFESSIONAL_QUALIFICATIONS]
    config.role_specific_criteria_weight = weights[ScoringDimension.ROLE_SPECIFIC_CRITERIA]


def build_config_fingerprint(weights: dict[ScoringDimension, Decimal]) -> str:
    payload = {
        "algorithm": ALGORITHM_VERSION,
        "weights": {
            dimension.value: decimal_text(weights[dimension]) for dimension in DIMENSION_ORDER
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def quantize_score(value: Decimal) -> Decimal:
    return value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def same_timestamp(left: datetime, right: datetime | None) -> bool:
    if right is None:
        return False
    left_value = left if left.tzinfo is not None else left.replace(tzinfo=UTC)
    right_value = right if right.tzinfo is not None else right.replace(tzinfo=UTC)
    return left_value.astimezone(UTC) == right_value.astimezone(UTC)
