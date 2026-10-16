from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.interview_probes import AssistanceKind, ProbeStatus
from app.schemas.processing_jobs import ProcessingJobResponse


class ProbeGap(BaseModel):
    requirement_id: UUID
    requirement_text: str
    match_id: UUID
    match_status: str
    required: bool
    priority: int
    reason: str
    score_id: UUID
    score_dimensions: list[str]
    cv_evidence_ids: list[UUID]
    cv_evidence: list[dict[str, str]] = Field(default_factory=list)
    f1_justification: str = ""
    f2_evidence: list[dict[str, str]] = Field(default_factory=list)
    dimension_weight: float = 0


class ProbeGenerationContext(BaseModel):
    interview_id: UUID
    application_id: UUID
    job_title: str
    jd_version: int
    gaps: list[ProbeGap]
    current_probes: list[str] = Field(default_factory=list)
    transcript: list[dict[str, str]] = Field(default_factory=list)
    request_text: str | None = None


class GeneratedProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    requirement_id: UUID
    question: str = Field(min_length=15, max_length=1200)
    reason: str = Field(min_length=10, max_length=1200)
    expected_evidence: list[str] = Field(min_length=1, max_length=8)
    transcript_segment_ids: list[UUID] = Field(default_factory=list, max_length=20)


class GeneratedProbeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probes: list[GeneratedProbe] = Field(min_length=1, max_length=5)


class CopilotResponse(GeneratedProbe):
    evidence_refs: dict


class GenerateProbesRequest(BaseModel):
    request_id: UUID
    regenerate: bool = False


class CopilotRequest(BaseModel):
    request_id: UUID
    message: str = Field(min_length=5, max_length=1500)


class ProbeStatusUpdate(BaseModel):
    status: ProbeStatus


class ProbeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    requirement_id: UUID
    requirement_text: str
    question: str
    reason: str
    expected_evidence: list[str]
    evidence_refs: dict
    priority: int
    status: ProbeStatus
    created_at: datetime
    updated_at: datetime
    is_current: bool = True


class AssistanceRunResponse(BaseModel):
    id: UUID
    kind: AssistanceKind
    task: ProcessingJobResponse
    response: CopilotResponse | None


class InterviewAssistanceResponse(BaseModel):
    interview_id: UUID
    candidate_reference: str
    job_title: str
    skills: list[str]
    experience: list[str]
    qualifications: list[str]
    gaps: list[ProbeGap]
    probes: list[ProbeResponse]
    runs: list[AssistanceRunResponse]
    guidance: str = (
        "Decision support only. Expected evidence is not a canonical answer "
        "or hiring recommendation."
    )
