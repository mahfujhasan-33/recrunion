from typing import Protocol, runtime_checkable

from app.schemas.assistant import AssistantTurnPlan, AssistantTurnRequest
from app.schemas.candidate_profiles import (
    CandidateProfileExtractionRequest,
    CandidateProfileExtractionResult,
)
from app.schemas.job_descriptions import (
    JobDescriptionEnhancementRequest,
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
    PolicyAlignmentRequest,
    PolicyAlignmentResult,
)


@runtime_checkable
class LLMAdapter(Protocol):
    """Application contract for job-description generation providers."""

    async def plan_assistant_turn(
        self,
        request: AssistantTurnRequest,
    ) -> AssistantTurnPlan:
        """Interpret a recruiter message as a bounded assistant action."""

        ...

    async def generate_job_description(
        self,
        request: JobDescriptionGenerationRequest,
    ) -> JobDescriptionGenerationResult:
        """Generate a structured job description from recruiter requirements."""

        ...

    async def enhance_job_description(
        self,
        request: JobDescriptionEnhancementRequest,
    ) -> JobDescriptionGenerationResult:
        """Revise a job description using evidence-backed policy findings."""

        ...

    async def evaluate_policy_alignment(
        self,
        request: PolicyAlignmentRequest,
    ) -> PolicyAlignmentResult:
        """Evaluate one JD against explicitly supplied policy evidence."""

        ...

    async def extract_candidate_profile(
        self,
        request: CandidateProfileExtractionRequest,
    ) -> CandidateProfileExtractionResult:
        """Extract an evidence-backed candidate profile from supplied CV chunks."""

        ...
