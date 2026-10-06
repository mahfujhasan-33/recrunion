from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

DescriptionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
DescriptionItem = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
ReviewedDescription = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=50, max_length=30_000),
]


class JobDescriptionGenerationRequest(BaseModel):
    """Provider-neutral structured job requirements."""

    model_config = ConfigDict(frozen=True)

    job_id: UUID
    title: str
    location: str
    employment_type: str
    application_email: EmailStr
    required_skills: list[str]
    preferred_skills: list[str]
    minimum_experience: float | None
    qualifications: list[str]
    selection_criteria: list[str]


class GeneratedJobDescription(BaseModel):
    """Structured output required from an LLM provider."""

    title: DescriptionItem
    summary: DescriptionText
    responsibilities: list[DescriptionItem] = Field(min_length=1, max_length=20)
    required_skills: list[DescriptionItem] = Field(min_length=1, max_length=50)
    preferred_skills: list[DescriptionItem] = Field(default_factory=list, max_length=50)
    qualifications: list[DescriptionItem] = Field(default_factory=list, max_length=50)
    minimum_experience: float | None = Field(default=None, ge=0, le=80)
    application_information: DescriptionText | None = None


class LLMGenerationMetadata(BaseModel):
    """Safe provider metadata retained for generation traceability."""

    finish_reason: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class JobDescriptionGenerationResult(BaseModel):
    """Provider-neutral generation result returned by an LLM adapter."""

    description: GeneratedJobDescription
    provider: str
    model: str
    metadata: LLMGenerationMetadata = Field(default_factory=LLMGenerationMetadata)


class JobDescriptionUpdateRequest(BaseModel):
    """Recruiter-reviewed job description content."""

    content: ReviewedDescription
