import logging
import re
from datetime import UTC, datetime
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from app.adapters.llm import LLMAdapter
from app.errors import (
    InvalidJobStatusError,
    JobDescriptionValidationError,
    JobNotFoundError,
    LLMProviderError,
    RecrUnionError,
)
from app.models.jobs import (
    Job,
    JobStatus,
    RequirementCategory,
    RequirementType,
)
from app.repositories.jobs import JobRepository
from app.schemas.job_descriptions import (
    GeneratedJobDescription,
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
)

PROTECTED_REQUIREMENT_TERMS = {
    "age",
    "ethnicity",
    "gender",
    "marital status",
    "nationality",
    "pregnancy",
    "race",
    "religion",
}

logger = logging.getLogger(__name__)


class JobDescriptionState(TypedDict):
    """State passed through the job-description generation graph."""

    job_id: UUID
    attempt_count: int
    validation_errors: list[str]
    job: NotRequired[Job]
    requirements: NotRequired[JobDescriptionGenerationRequest]
    generated_description: NotRequired[GeneratedJobDescription]
    rendered_content: NotRequired[str]
    model_metadata: NotRequired[dict[str, object]]
    generation_result: NotRequired[JobDescriptionGenerationResult]
    error: RecrUnionError | None


class JobDescriptionGraph:
    """Coordinate validated, persisted AI job-description generation."""

    def __init__(
        self,
        repository: JobRepository,
        llm_adapter: LLMAdapter,
        *,
        max_attempts: int = 2,
    ) -> None:
        self._repository = repository
        self._llm_adapter = llm_adapter
        self._max_attempts = max_attempts
        self._graph = self._build_graph()

    async def run(self, job_id: UUID) -> JobDescriptionState:
        """Execute generation and return its final typed state."""

        initial_state: JobDescriptionState = {
            "job_id": job_id,
            "attempt_count": 0,
            "validation_errors": [],
            "error": None,
        }
        return await self._graph.ainvoke(initial_state)

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(JobDescriptionState)
        builder.add_node("load_job_requirements", self._load_job_requirements)
        builder.add_node("validate_requirements", self._validate_requirements)
        builder.add_node("generate_job_description", self._generate_job_description)
        builder.add_node(
            "validate_generated_description",
            self._validate_generated_description,
        )
        builder.add_node(
            "persist_generated_description",
            self._persist_generated_description,
        )
        builder.add_edge(START, "load_job_requirements")
        builder.add_conditional_edges(
            "load_job_requirements",
            self._route_after_validation,
            {"continue": "validate_requirements", "fail": END},
        )
        builder.add_conditional_edges(
            "validate_requirements",
            self._route_after_validation,
            {"continue": "generate_job_description", "fail": END},
        )
        builder.add_conditional_edges(
            "generate_job_description",
            self._route_after_generation,
            {
                "continue": "validate_generated_description",
                "retry": "generate_job_description",
                "fail": END,
            },
        )
        builder.add_conditional_edges(
            "validate_generated_description",
            self._route_after_validation,
            {"continue": "persist_generated_description", "fail": END},
        )
        builder.add_edge("persist_generated_description", END)
        return builder.compile()

    def _load_job_requirements(self, state: JobDescriptionState) -> JobDescriptionState:
        job = self._repository.get(state["job_id"])
        if job is None:
            return {**state, "error": JobNotFoundError(state["job_id"])}

        required_skills: list[str] = []
        preferred_skills: list[str] = []
        qualifications: list[str] = []
        selection_criteria: list[str] = []
        minimum_experience: float | None = None

        for requirement in job.requirements:
            if requirement.category == RequirementCategory.SKILL:
                target = (
                    required_skills
                    if requirement.requirement_type == RequirementType.REQUIRED
                    else preferred_skills
                )
                target.append(requirement.text)
            elif requirement.category == RequirementCategory.EXPERIENCE:
                if requirement.minimum_value is not None:
                    minimum_experience = float(requirement.minimum_value)
            elif requirement.category == RequirementCategory.QUALIFICATION:
                qualifications.append(requirement.text)
            elif requirement.category == RequirementCategory.SELECTION_CRITERION:
                selection_criteria.append(requirement.text)

        request = JobDescriptionGenerationRequest(
            job_id=job.id,
            title=job.title,
            location=job.location,
            employment_type=job.employment_type.value,
            application_email=job.application_email,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            minimum_experience=minimum_experience,
            qualifications=qualifications,
            selection_criteria=selection_criteria,
        )
        return {**state, "job": job, "requirements": request, "error": None}

    @staticmethod
    def _validate_requirements(state: JobDescriptionState) -> JobDescriptionState:
        job = state["job"]
        request = state["requirements"]
        errors: list[str] = []

        if job.status != JobStatus.DRAFT:
            return {
                **state,
                "error": InvalidJobStatusError(
                    "A job description can only be generated for a DRAFT job."
                ),
            }
        if not request.required_skills:
            errors.append("At least one required skill is needed.")

        requirement_text = " ".join(
            [
                *request.required_skills,
                *request.preferred_skills,
                *request.qualifications,
                *request.selection_criteria,
            ]
        )
        if _contains_protected_requirement(requirement_text):
            errors.append("Job requirements must not use protected characteristics.")

        if errors:
            return {
                **state,
                "validation_errors": errors,
                "error": JobDescriptionValidationError(" ".join(errors)),
            }
        return {**state, "validation_errors": [], "error": None}

    async def _generate_job_description(
        self,
        state: JobDescriptionState,
    ) -> JobDescriptionState:
        attempt_count = state["attempt_count"] + 1
        try:
            result = await self._llm_adapter.generate_job_description(state["requirements"])
        except LLMProviderError as error:
            return {**state, "attempt_count": attempt_count, "error": error}

        return {
            **state,
            "attempt_count": attempt_count,
            "generation_result": result,
            "generated_description": result.description,
            "model_metadata": result.metadata.model_dump(exclude_none=True),
            "error": None,
        }

    @staticmethod
    def _validate_generated_description(
        state: JobDescriptionState,
    ) -> JobDescriptionState:
        request = state["requirements"]
        description = _apply_authoritative_requirements(
            request,
            state["generated_description"],
        )
        errors: list[str] = []

        generated_text = " ".join([description.summary, *description.responsibilities])
        if _contains_protected_requirement(generated_text):
            errors.append("Generated content contains protected-characteristic language.")

        if errors:
            logger.warning(
                "Generated job description failed validation",
                extra={
                    "job_id": str(state["job_id"]),
                    "validation_errors": errors,
                },
            )
            return {
                **state,
                "generated_description": description,
                "validation_errors": errors,
                "error": JobDescriptionValidationError(
                    "The generated job description did not pass validation."
                ),
            }

        return {
            **state,
            "generated_description": description,
            "rendered_content": render_job_description(description),
            "validation_errors": [],
            "error": None,
        }

    def _persist_generated_description(
        self,
        state: JobDescriptionState,
    ) -> JobDescriptionState:
        job = state["job"]
        result = state["generation_result"]
        generated_at = datetime.now(UTC)

        job.jd_generated_content = state["rendered_content"]
        job.jd_content = state["rendered_content"]
        job.jd_version = 1
        job.jd_generated_at = generated_at
        job.approved_at = None
        job.jd_provider = result.provider
        job.jd_model = result.model
        job.jd_generation_metadata = result.metadata.model_dump(exclude_none=True)
        job.status = JobStatus.GENERATED
        persisted = self._repository.update(job)
        return {**state, "job": persisted, "error": None}

    @staticmethod
    def _route_after_validation(state: JobDescriptionState) -> Literal["continue", "fail"]:
        return "fail" if state.get("error") is not None else "continue"

    def _route_after_generation(
        self,
        state: JobDescriptionState,
    ) -> Literal["continue", "retry", "fail"]:
        error = state.get("error")
        if error is None:
            return "continue"
        if (
            isinstance(error, LLMProviderError)
            and error.retryable
            and state["attempt_count"] < self._max_attempts
        ):
            return "retry"
        return "fail"


def render_job_description(description: GeneratedJobDescription) -> str:
    """Render validated structured output into recruiter-editable Markdown."""

    sections = [
        f"# {description.title}",
        "## Summary\n" + description.summary,
        "## Responsibilities\n" + _render_items(description.responsibilities),
        "## Required skills\n" + _render_items(description.required_skills),
    ]
    if description.preferred_skills:
        sections.append("## Preferred skills\n" + _render_items(description.preferred_skills))
    if description.qualifications:
        sections.append("## Qualifications\n" + _render_items(description.qualifications))
    if description.minimum_experience is not None:
        sections.append(
            "## Experience\n"
            f"Minimum {description.minimum_experience:g} years of relevant experience."
        )
    if description.application_information:
        sections.append("## How to apply\n" + description.application_information)
    return "\n\n".join(sections)


def _apply_authoritative_requirements(
    request: JobDescriptionGenerationRequest,
    description: GeneratedJobDescription,
) -> GeneratedJobDescription:
    """Replace model-repeated requirements with recruiter-approved values."""

    return description.model_copy(
        update={
            "title": request.title,
            "required_skills": request.required_skills,
            "preferred_skills": request.preferred_skills,
            "qualifications": request.qualifications,
            "minimum_experience": request.minimum_experience,
            "application_information": (f"Send your application to {request.application_email}."),
        }
    )


def _contains_protected_requirement(text: str) -> bool:
    normalized = text.casefold()
    return any(
        re.search(rf"\b{re.escape(term)}\b", normalized) for term in PROTECTED_REQUIREMENT_TERMS
    )


def _render_items(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)
