from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator

from app.models.jobs import EmploymentType, JobStatus

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
RequirementText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class JobWriteRequest(BaseModel):
    title: ShortText
    location: ShortText
    employment_type: EmploymentType
    application_email: EmailStr
    required_skills: list[RequirementText] = Field(min_length=1, max_length=50)
    preferred_skills: list[RequirementText] = Field(default_factory=list, max_length=50)
    minimum_experience: float | None = Field(default=None, ge=0, le=80)
    qualifications: list[RequirementText] = Field(default_factory=list, max_length=50)
    selection_criteria: list[RequirementText] = Field(default_factory=list, max_length=50)

    @field_validator(
        "required_skills",
        "preferred_skills",
        "qualifications",
        "selection_criteria",
    )
    @classmethod
    def reject_duplicate_requirements(cls, values: list[str]) -> list[str]:
        normalized = [value.casefold() for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Requirement entries must be unique within each group.")
        return values


class JobResponse(JobWriteRequest):
    id: UUID
    code: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
