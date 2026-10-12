from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.jobs import RequirementCategory, RequirementType
from app.models.screening import CandidateScreeningStatus, RequirementMatchStatus
from app.schemas.candidate_profiles import CandidateProfileData


class ScreeningRequirement(BaseModel):
    requirement_id: UUID
    category: RequirementCategory
    requirement_type: RequirementType
    text: str = Field(min_length=1, max_length=500)
    minimum_value: Decimal | None = None
    priority: int = Field(ge=1)
    evidence_chunk_ids: list[UUID] = Field(default_factory=list, max_length=5)


class ScreeningEvidence(BaseModel):
    evidence_id: UUID
    document_id: UUID
    page_number: int = Field(ge=1)
    content: str = Field(min_length=1)
    similarity: float = Field(ge=-1, le=1)


class CandidateScreeningEvaluationRequest(BaseModel):
    job_title: str
    candidate_profile: CandidateProfileData
    requirements: list[ScreeningRequirement] = Field(min_length=1)
    evidence: list[ScreeningEvidence] = Field(default_factory=list)


class GeneratedRequirementMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: UUID
    status: RequirementMatchStatus
    justification: str = Field(min_length=1, max_length=1200)
    evidence_chunk_ids: list[UUID] = Field(default_factory=list, max_length=5)


class CandidateScreeningEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[GeneratedRequirementMatch] = Field(min_length=1)


class CandidateScreeningEvaluationResult(BaseModel):
    evaluation: CandidateScreeningEvaluation
    provider: str
    model: str


class ScreeningSummary(BaseModel):
    met: int = Field(ge=0)
    partially_met: int = Field(ge=0)
    unmet: int = Field(ge=0)


class ScreeningStartResponse(BaseModel):
    application_id: UUID
    screening_id: UUID
    task_id: UUID
    status: CandidateScreeningStatus


class ScreeningBatchResponse(BaseModel):
    eligible: int = Field(ge=0)
    queued: int = Field(ge=0)
    current: int = Field(ge=0)
    active: int = Field(ge=0)
    ineligible: int = Field(ge=0)
    tasks: list[ScreeningStartResponse]


class ScreeningEvidenceResponse(BaseModel):
    chunk_id: UUID
    page_number: int
    excerpt: str
    similarity: float


class RequirementMatchResponse(BaseModel):
    requirement_id: UUID
    category: RequirementCategory
    requirement_type: RequirementType
    requirement_text: str
    minimum_value: Decimal | None
    status: RequirementMatchStatus
    justification: str
    evidence: list[ScreeningEvidenceResponse]


class CandidateScreeningResponse(BaseModel):
    screening_id: UUID
    job_id: UUID
    application_id: UUID
    candidate_reference: str
    status: CandidateScreeningStatus
    is_current: bool
    rank: int | None
    ranking_explanation: str | None
    required: ScreeningSummary | None
    preferred: ScreeningSummary | None
    matches: list[RequirementMatchResponse]
    safe_error_code: str | None
    safe_error_message: str | None
    screened_at: datetime | None


class ScreeningRankingEntry(BaseModel):
    application_id: UUID
    candidate_reference: str
    screening_id: UUID | None
    screening_status: CandidateScreeningStatus | None
    is_current: bool
    rank: int | None
    ranking_explanation: str
    required: ScreeningSummary | None
    preferred: ScreeningSummary | None
    safe_error_message: str | None


class JobScreeningResponse(BaseModel):
    job_id: UUID
    ranked: list[ScreeningRankingEntry]
    unranked: list[ScreeningRankingEntry]
