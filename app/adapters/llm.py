from typing import Protocol, runtime_checkable

from app.schemas.job_descriptions import (
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
)


@runtime_checkable
class LLMAdapter(Protocol):
    """Application contract for job-description generation providers."""

    async def generate_job_description(
        self,
        request: JobDescriptionGenerationRequest,
    ) -> JobDescriptionGenerationResult:
        """Generate a structured job description from recruiter requirements."""

        ...
