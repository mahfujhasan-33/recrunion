from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.models.job_policy import JobDescriptionSection, PolicyAlignmentStatus

FindingExplanation = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)
]


class PolicyEvidence(BaseModel):
    evidence_id: UUID
    document_id: UUID
    document_filename: str
    document_type: str
    document_checksum: str
    content: str
    page_number: int | None = None
    section_title: str | None = None
    similarity: float = Field(ge=-1, le=1)


class GeneratedPolicyFinding(BaseModel):
    evidence_id: UUID
    related_jd_section: JobDescriptionSection
    status: PolicyAlignmentStatus
    explanation: FindingExplanation


class PolicyAlignmentEvaluation(BaseModel):
    findings: list[GeneratedPolicyFinding] = Field(default_factory=list, max_length=20)


class PolicyFindingResponse(BaseModel):
    id: UUID
    source_document_id: UUID
    source_chunk_id: UUID
    source_filename: str
    source_document_type: str
    source_available: bool
    evidence_excerpt: str
    page_number: int | None
    section_title: str | None
    related_jd_section: JobDescriptionSection
    status: PolicyAlignmentStatus
    explanation: str


class PolicyReviewResponse(BaseModel):
    id: UUID | None
    job_id: UUID
    jd_version: int
    is_current: bool
    retrieval_count: int
    actionable_finding_count: int
    created_at: datetime | None
    findings: list[PolicyFindingResponse]
    message: str | None = None
