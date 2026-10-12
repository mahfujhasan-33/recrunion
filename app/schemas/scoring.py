from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.jobs import RequirementType
from app.models.scoring import EvidenceCoverage, ScoringDimension
from app.models.screening import RequirementMatchStatus
from app.schemas.screening import ScreeningEvidenceResponse


class ScoringConfigUpdate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    technical_skills_weight: Decimal = Field(gt=0, max_digits=10, decimal_places=4)
    relevant_experience_weight: Decimal = Field(gt=0, max_digits=10, decimal_places=4)
    qualifications_weight: Decimal = Field(gt=0, max_digits=10, decimal_places=4)
    role_specific_criteria_weight: Decimal = Field(gt=0, max_digits=10, decimal_places=4)


class ScoringConfigDimension(BaseModel):
    dimension: ScoringDimension
    configured_weight: Decimal
    applicable: bool
    effective_weight: Decimal | None


class ScoringConfigResponse(BaseModel):
    job_id: UUID
    version: int
    dimensions: list[ScoringConfigDimension]
    updated_at: datetime


class ScoreRequirementSnapshot(BaseModel):
    match_id: UUID
    requirement_id: UUID
    requirement_text: str
    requirement_type: RequirementType
    match_status: RequirementMatchStatus
    points: Decimal
    importance: Decimal


class ScoreRequirementResponse(ScoreRequirementSnapshot):
    evidence: list[ScreeningEvidenceResponse]


class ExcludedScoringRequirement(BaseModel):
    match_id: UUID
    requirement_id: UUID
    requirement_text: str
    reason: str


class DimensionBreakdownSnapshot(BaseModel):
    dimension: ScoringDimension
    applicable: bool
    raw_score: Decimal | None
    configured_weight: Decimal
    effective_weight: Decimal | None
    weighted_contribution: Decimal | None
    justification: str
    requirements: list[ScoreRequirementSnapshot]


class DimensionBreakdownResponse(BaseModel):
    dimension: ScoringDimension
    applicable: bool
    raw_score: Decimal | None
    configured_weight: Decimal
    effective_weight: Decimal | None
    weighted_contribution: Decimal | None
    justification: str
    requirements: list[ScoreRequirementResponse]


class CandidateScoreSnapshot(BaseModel):
    dimensions: list[DimensionBreakdownSnapshot]
    excluded_requirements: list[ExcludedScoringRequirement] = Field(default_factory=list)


class CandidateScoreResponse(BaseModel):
    score_id: UUID
    job_id: UUID
    application_id: UUID
    candidate_reference: str
    match_rank: int | None
    suitability_score: Decimal
    required_gaps: int
    evidence_coverage: EvidenceCoverage
    is_current: bool
    stale_reason: str | None
    dimensions: list[DimensionBreakdownResponse]
    excluded_requirements: list[ExcludedScoringRequirement]
    justification: str
    scoring_config_version: int
    algorithm_version: str
    calculated_at: datetime


class DimensionScoreSummary(BaseModel):
    dimension: ScoringDimension
    applicable: bool
    raw_score: Decimal | None


class CandidateScoreListEntry(BaseModel):
    application_id: UUID
    candidate_reference: str
    match_rank: int | None
    suitability_score: Decimal | None
    required_gaps: int | None
    evidence_coverage: EvidenceCoverage | None
    is_current: bool
    stale_reason: str | None
    dimensions: list[DimensionScoreSummary]


class JobScoresResponse(BaseModel):
    job_id: UUID
    candidates: list[CandidateScoreListEntry]


class ScoreBatchResponse(BaseModel):
    eligible: int = Field(ge=0)
    scored: int = Field(ge=0)
    already_current: int = Field(ge=0)
    stale_replaced: int = Field(ge=0)
    skipped: int = Field(ge=0)
    failed: int = Field(ge=0)
