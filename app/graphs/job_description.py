import asyncio
import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Literal, NotRequired, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph

from app.adapters.embeddings import EmbeddingAdapter
from app.adapters.llm import LLMAdapter
from app.errors import (
    InvalidJobStatusError,
    JobDescriptionValidationError,
    JobNotFoundError,
    LLMProviderError,
    PolicyRetrievalError,
    RecrUnionError,
)
from app.models.job_policy import JobDescriptionPolicyFinding, JobDescriptionPolicyReview
from app.models.jobs import Job, JobStatus, RequirementCategory, RequirementType
from app.repositories.jobs import JobRepository
from app.repositories.policy_knowledge import PolicyKnowledgeRepository, PolicyReviewRepository
from app.schemas.job_descriptions import (
    GeneratedJobDescription,
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
    LLMGenerationMetadata,
    PolicyAlignmentRequest,
    PolicyAlignmentResult,
)
from app.schemas.policy_findings import (
    GeneratedPolicyFinding,
    PolicyAlignmentEvaluation,
    PolicyEvidence,
)

PROTECTED_REQUIREMENT_TERMS = (
    "age",
    "disability",
    "ethnicity",
    "gender",
    "gender identity",
    "female",
    "male",
    "marital status",
    "men",
    "nationality",
    "pregnancy",
    "race",
    "religion",
    "sex",
    "sexual orientation",
    "women",
)

_PROTECTED_TERM_PATTERN = (
    "(?:"
    + "|".join(
        re.escape(term) for term in sorted(PROTECTED_REQUIREMENT_TERMS, key=len, reverse=True)
    )
    + ")"
)
_SAFE_PROTECTED_CONTEXT_PATTERN = re.compile(
    r"\b(?:equal[- ]opportunity|does not discriminate|do not discriminate|"
    r"without regard to|regardless of|reasonable accommodations?|"
    r"all qualified applicants)\b",
    re.IGNORECASE,
)
_HARD_PROTECTED_RESTRICTION_PATTERNS = (
    re.compile(
        rf"\b(?:only|exclusively|must be|required to be|preferred)\b.{{0,60}}"
        rf"\b{_PROTECTED_TERM_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_PROTECTED_TERM_PATTERN}\b.{{0,60}}"
        r"\b(?:only|exclusively|required|preferred|ineligible|excluded|rejected)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:under|over|below|above|between)\b.{0,30}\bage\b|"
        r"\bage\b.{0,30}\b(?:under|over|below|above|between)\b",
        re.IGNORECASE,
    ),
)
_PROTECTED_INFLUENCE_PATTERNS = (
    re.compile(
        rf"\b(?:based on|because of|according to|on the basis of)\b.{{0,60}}"
        rf"\b{_PROTECTED_TERM_PATTERN}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_PROTECTED_TERM_PATTERN}\b.{{0,60}}"
        r"\b(?:determines?|affects?|influences?)\b.{{0,30}}"
        r"\b(?:selection|eligibility|hiring)\b",
        re.IGNORECASE,
    ),
)

logger = logging.getLogger(__name__)


class JobDescriptionState(TypedDict):
    job_id: UUID
    generation_attempt_count: int
    policy_attempt_count: int
    validation_errors: list[str]
    job: NotRequired[Job]
    requirements: NotRequired[JobDescriptionGenerationRequest]
    retrieval_query: NotRequired[str]
    policy_evidence: NotRequired[list[PolicyEvidence]]
    generated_description: NotRequired[GeneratedJobDescription]
    rendered_content: NotRequired[str]
    generation_result: NotRequired[JobDescriptionGenerationResult]
    policy_result: NotRequired[PolicyAlignmentResult]
    error: RecrUnionError | None


class JobDescriptionGraph:
    """Generate a JD and evidence-backed company-policy review."""

    def __init__(
        self,
        repository: JobRepository,
        llm_adapter: LLMAdapter,
        policy_repository: PolicyKnowledgeRepository,
        policy_review_repository: PolicyReviewRepository,
        embedding_adapter: EmbeddingAdapter,
        *,
        max_attempts: int = 2,
        retrieval_candidate_count: int = 20,
        retrieval_top_k: int = 8,
        retrieval_min_similarity: float = 0.3,
    ) -> None:
        self._repository = repository
        self._llm_adapter = llm_adapter
        self._policy_repository = policy_repository
        self._policy_review_repository = policy_review_repository
        self._embedding_adapter = embedding_adapter
        self._max_attempts = max_attempts
        self._retrieval_candidate_count = retrieval_candidate_count
        self._retrieval_top_k = retrieval_top_k
        self._retrieval_min_similarity = retrieval_min_similarity
        self._graph = self._build_graph()

    async def run(self, job_id: UUID) -> JobDescriptionState:
        return await self._graph.ainvoke(
            {
                "job_id": job_id,
                "generation_attempt_count": 0,
                "policy_attempt_count": 0,
                "validation_errors": [],
                "error": None,
            }
        )

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(JobDescriptionState)
        builder.add_node("load_job_requirements", self._load_job_requirements)
        builder.add_node("validate_requirements", self._validate_requirements)
        builder.add_node("build_policy_retrieval_query", self._build_policy_retrieval_query)
        builder.add_node("retrieve_company_policy_context", self._retrieve_policy_context)
        builder.add_node("generate_job_description", self._generate_job_description)
        builder.add_node("validate_generated_description", self._validate_generated_description)
        builder.add_node("evaluate_policy_alignment", self._evaluate_policy_alignment)
        builder.add_node("validate_policy_findings", self._validate_policy_findings)
        builder.add_node("persist_generated_description", self._persist_generated_description)
        builder.add_edge(START, "load_job_requirements")
        for source, target in (
            ("load_job_requirements", "validate_requirements"),
            ("validate_requirements", "build_policy_retrieval_query"),
            ("build_policy_retrieval_query", "retrieve_company_policy_context"),
            ("retrieve_company_policy_context", "generate_job_description"),
        ):
            builder.add_conditional_edges(
                source,
                self._route_after_validation,
                {"continue": target, "fail": END},
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
            {"continue": "evaluate_policy_alignment", "fail": END},
        )
        builder.add_conditional_edges(
            "evaluate_policy_alignment",
            self._route_after_policy_evaluation,
            {
                "continue": "validate_policy_findings",
                "retry": "evaluate_policy_alignment",
                "fail": END,
            },
        )
        builder.add_conditional_edges(
            "validate_policy_findings",
            self._route_after_validation,
            {"continue": "persist_generated_description", "fail": END},
        )
        builder.add_edge("persist_generated_description", END)
        return builder.compile()

    def _load_job_requirements(self, state: JobDescriptionState) -> JobDescriptionState:
        job = self._repository.get(state["job_id"])
        if job is None:
            return {**state, "error": JobNotFoundError(state["job_id"])}
        return {
            **state,
            "job": job,
            "requirements": requirements_from_job(job),
            "error": None,
        }

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
        if contains_protected_requirement(requirement_text):
            errors.append("Job requirements must not use protected characteristics.")
        if errors:
            return {
                **state,
                "validation_errors": errors,
                "error": JobDescriptionValidationError(" ".join(errors)),
            }
        return {**state, "validation_errors": [], "error": None}

    @staticmethod
    def _build_policy_retrieval_query(state: JobDescriptionState) -> JobDescriptionState:
        return {
            **state,
            "retrieval_query": build_policy_retrieval_query(state["requirements"]),
            "error": None,
        }

    async def _retrieve_policy_context(self, state: JobDescriptionState) -> JobDescriptionState:
        try:
            if not self._policy_repository.has_ready_documents(self._embedding_adapter.model_name):
                return {
                    **state,
                    "requirements": state["requirements"].model_copy(
                        update={"policy_evidence": []}
                    ),
                    "policy_evidence": [],
                    "error": None,
                }
            query_embedding = await asyncio.to_thread(
                self._embedding_adapter.embed_query,
                state["retrieval_query"],
            )
            evidence = self._policy_repository.retrieve(
                query_embedding,
                embedding_model=self._embedding_adapter.model_name,
                candidate_count=self._retrieval_candidate_count,
                top_k=self._retrieval_top_k,
                minimum_similarity=self._retrieval_min_similarity,
            )
        except RecrUnionError as error:
            return {**state, "error": error}
        except Exception:
            logger.exception(
                "Company-policy retrieval failed",
                extra={"job_id": str(state["job_id"]), "operation": "policy_retrieval"},
            )
            return {
                **state,
                "error": PolicyRetrievalError("Company-policy retrieval failed."),
            }
        requirements = state["requirements"].model_copy(update={"policy_evidence": evidence})
        return {**state, "requirements": requirements, "policy_evidence": evidence, "error": None}

    async def _generate_job_description(self, state: JobDescriptionState) -> JobDescriptionState:
        attempt_count = state["generation_attempt_count"] + 1
        try:
            result = await self._llm_adapter.generate_job_description(state["requirements"])
        except LLMProviderError as error:
            return {**state, "generation_attempt_count": attempt_count, "error": error}
        return {
            **state,
            "generation_attempt_count": attempt_count,
            "generation_result": result,
            "generated_description": result.description,
            "error": None,
        }

    @staticmethod
    def _validate_generated_description(state: JobDescriptionState) -> JobDescriptionState:
        description = apply_authoritative_requirements(
            state["requirements"], state["generated_description"]
        )
        generated_text = " ".join([description.summary, *description.responsibilities])
        if contains_protected_requirement(generated_text):
            return {
                **state,
                "generated_description": description,
                "validation_errors": [
                    "Generated content contains protected-characteristic language."
                ],
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

    async def _evaluate_policy_alignment(self, state: JobDescriptionState) -> JobDescriptionState:
        evidence = state.get("policy_evidence", [])
        if not evidence:
            generation = state["generation_result"]
            return {
                **state,
                "policy_result": PolicyAlignmentResult(
                    evaluation=PolicyAlignmentEvaluation(findings=[]),
                    provider=generation.provider,
                    model=generation.model,
                    metadata=LLMGenerationMetadata(),
                ),
                "error": None,
            }
        try:
            attempt_count = state["policy_attempt_count"] + 1
            result = await self._llm_adapter.evaluate_policy_alignment(
                PolicyAlignmentRequest(
                    job_id=state["job_id"],
                    content=state["rendered_content"],
                    requirements=state["requirements"],
                    evidence=evidence,
                )
            )
        except LLMProviderError as error:
            return {**state, "policy_attempt_count": attempt_count, "error": error}
        return {
            **state,
            "policy_attempt_count": attempt_count,
            "policy_result": result,
            "error": None,
        }

    @staticmethod
    def _validate_policy_findings(state: JobDescriptionState) -> JobDescriptionState:
        expected_ids = {item.evidence_id for item in state.get("policy_evidence", [])}
        finding_ids = [item.evidence_id for item in state["policy_result"].evaluation.findings]
        if len(finding_ids) != len(expected_ids) or set(finding_ids) != expected_ids:
            return {
                **state,
                "validation_errors": ["Policy findings did not reference every retrieved source."],
                "error": JobDescriptionValidationError(
                    "The policy-alignment result did not pass evidence validation."
                ),
            }
        return {**state, "validation_errors": [], "error": None}

    def _persist_generated_description(self, state: JobDescriptionState) -> JobDescriptionState:
        job = state["job"]
        generation = state["generation_result"]
        content = state["rendered_content"]
        job.jd_generated_content = content
        job.jd_content = content
        job.jd_version = 1
        job.jd_generated_at = datetime.now(UTC)
        job.approved_at = None
        job.jd_provider = generation.provider
        job.jd_model = generation.model
        job.jd_generation_metadata = generation.metadata.model_dump(exclude_none=True)
        job.status = JobStatus.GENERATED
        review = build_policy_review(
            job,
            content,
            state.get("policy_evidence", []),
            state["policy_result"],
            self._embedding_adapter.model_name,
        )
        self._policy_review_repository.save(review)
        return {**state, "job": job, "error": None}

    @staticmethod
    def _route_after_validation(state: JobDescriptionState) -> Literal["continue", "fail"]:
        return "fail" if state.get("error") is not None else "continue"

    def _route_after_generation(
        self, state: JobDescriptionState
    ) -> Literal["continue", "retry", "fail"]:
        error = state.get("error")
        if error is None:
            return "continue"
        if (
            isinstance(error, LLMProviderError)
            and error.retryable
            and state["generation_attempt_count"] < self._max_attempts
        ):
            return "retry"
        return "fail"

    def _route_after_policy_evaluation(
        self, state: JobDescriptionState
    ) -> Literal["continue", "retry", "fail"]:
        error = state.get("error")
        if error is None:
            return "continue"
        if (
            isinstance(error, LLMProviderError)
            and error.retryable
            and state["policy_attempt_count"] < self._max_attempts
        ):
            return "retry"
        return "fail"


def build_policy_retrieval_query(request: JobDescriptionGenerationRequest) -> str:
    parts = [
        f"Role: {request.title}",
        f"Location: {request.location}",
        f"Employment type: {request.employment_type}",
        "Required skills: " + "; ".join(request.required_skills),
        "Preferred skills: " + "; ".join(request.preferred_skills),
        "Qualifications: " + "; ".join(request.qualifications),
        "Selection criteria: " + "; ".join(request.selection_criteria),
    ]
    if request.minimum_experience is not None:
        parts.append(f"Minimum experience: {request.minimum_experience:g} years")
    parts.append("Relevant hiring policies, qualification rules, and job-description standards")
    return "\n".join(parts)


def render_job_description(description: GeneratedJobDescription) -> str:
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


def requirements_from_job(job: Job) -> JobDescriptionGenerationRequest:
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
    return JobDescriptionGenerationRequest(
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


def build_policy_review(
    job: Job,
    content: str,
    evidence: list[PolicyEvidence],
    result: PolicyAlignmentResult,
    embedding_model: str,
) -> JobDescriptionPolicyReview:
    evidence_by_id = {item.evidence_id: item for item in evidence}
    return JobDescriptionPolicyReview(
        job_id=job.id,
        jd_version=job.jd_version,
        created_at=datetime.now(UTC),
        jd_content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        retrieval_count=len(evidence),
        embedding_model=embedding_model,
        provider=result.provider,
        model=result.model,
        findings=[
            _finding_from_evidence(finding, evidence_by_id[finding.evidence_id])
            for finding in result.evaluation.findings
        ],
    )


def _finding_from_evidence(
    finding: GeneratedPolicyFinding, evidence: PolicyEvidence
) -> JobDescriptionPolicyFinding:
    return JobDescriptionPolicyFinding(
        source_document_id=evidence.document_id,
        source_chunk_id=evidence.evidence_id,
        source_filename=evidence.document_filename,
        source_document_type=evidence.document_type,
        source_checksum=evidence.document_checksum,
        evidence_excerpt=evidence.content,
        page_number=evidence.page_number,
        section_title=evidence.section_title,
        retrieval_similarity=evidence.similarity,
        related_jd_section=finding.related_jd_section,
        status=finding.status,
        explanation=finding.explanation,
    )


def apply_authoritative_requirements(
    request: JobDescriptionGenerationRequest,
    description: GeneratedJobDescription,
) -> GeneratedJobDescription:
    return description.model_copy(
        update={
            "title": request.title,
            "required_skills": request.required_skills,
            "preferred_skills": request.preferred_skills,
            "qualifications": request.qualifications,
            "minimum_experience": request.minimum_experience,
            "application_information": f"Send your application to {request.application_email}.",
        }
    )


def contains_protected_requirement(text: str) -> bool:
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text):
        if not re.search(rf"\b{_PROTECTED_TERM_PATTERN}\b", sentence, re.IGNORECASE):
            continue
        if any(pattern.search(sentence) for pattern in _HARD_PROTECTED_RESTRICTION_PATTERNS):
            return True
        if _SAFE_PROTECTED_CONTEXT_PATTERN.search(sentence):
            continue
        if any(pattern.search(sentence) for pattern in _PROTECTED_INFLUENCE_PATTERNS):
            return True
    return False


def _render_items(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)
