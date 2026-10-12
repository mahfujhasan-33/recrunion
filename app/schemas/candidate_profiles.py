from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContactDetailKind(StrEnum):
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    LOCATION = "LOCATION"
    WEBSITE = "WEBSITE"


class EvidenceBackedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_chunk_ids: list[UUID] = Field(min_length=1, max_length=10)


class CandidateSummary(EvidenceBackedItem):
    text: str = Field(min_length=1, max_length=2000)


class CandidateContactDetail(EvidenceBackedItem):
    kind: ContactDetailKind
    value: str = Field(min_length=1, max_length=500)


class CandidateEducation(EvidenceBackedItem):
    qualification: str = Field(min_length=1, max_length=500)
    institution: str | None = Field(default=None, max_length=500)
    field_of_study: str | None = Field(default=None, max_length=500)
    date_range: str | None = Field(default=None, max_length=200)


class CandidateWorkHistory(EvidenceBackedItem):
    role: str = Field(min_length=1, max_length=500)
    employer: str | None = Field(default=None, max_length=500)
    date_range: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class CandidateSkill(EvidenceBackedItem):
    name: str = Field(min_length=1, max_length=200)


class CandidateCertification(EvidenceBackedItem):
    name: str = Field(min_length=1, max_length=500)
    issuer: str | None = Field(default=None, max_length=500)
    date: str | None = Field(default=None, max_length=200)


class CandidateProject(EvidenceBackedItem):
    name: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=2000)
    technologies: list[str] = Field(default_factory=list, max_length=30)


class CandidateTechnology(EvidenceBackedItem):
    name: str = Field(min_length=1, max_length=200)


class CandidateProfileData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: CandidateSummary | None = None
    contact_details: list[CandidateContactDetail] = Field(default_factory=list, max_length=20)
    education: list[CandidateEducation] = Field(default_factory=list, max_length=30)
    work_history: list[CandidateWorkHistory] = Field(default_factory=list, max_length=50)
    skills: list[CandidateSkill] = Field(default_factory=list, max_length=100)
    certifications: list[CandidateCertification] = Field(default_factory=list, max_length=50)
    projects: list[CandidateProject] = Field(default_factory=list, max_length=50)
    technologies_tools: list[CandidateTechnology] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def require_supported_content(self) -> "CandidateProfileData":
        if self.summary is not None or any(
            (
                self.contact_details,
                self.education,
                self.work_history,
                self.skills,
                self.certifications,
                self.projects,
                self.technologies_tools,
            )
        ):
            return self
        raise ValueError("Candidate profile does not contain supported CV evidence.")


class CandidateProfileEvidence(BaseModel):
    evidence_id: UUID
    page_number: int = Field(ge=1)
    content: str = Field(min_length=1)


class CandidateProfileExtractionRequest(BaseModel):
    document_id: UUID
    candidate_reference: str
    evidence: list[CandidateProfileEvidence] = Field(min_length=1)


class CandidateProfileExtractionResult(BaseModel):
    profile: CandidateProfileData
    provider: str
    model: str
