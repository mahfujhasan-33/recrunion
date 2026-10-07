import asyncio
import logging
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
    PolicyEnhancementUnavailableError,
    PolicyRetrievalError,
    RecrUnionError,
)
from app.graphs.job_description import (
    apply_authoritative_requirements,
    build_policy_retrieval_query,
    build_policy_review,
    contains_protected_requirement,
    render_job_description,
    requirements_from_job,
)
from app.models.job_policy import PolicyAlignmentStatus
from app.models.jobs import Job, JobStatus
from app.repositories.jobs import JobRepository
from app.repositories.policy_knowledge import PolicyKnowledgeRepository, PolicyReviewRepository
from app.schemas.job_descriptions import (
    GeneratedJobDescription,
    JobDescriptionEnhancementRequest,
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
    PolicyAlignmentRequest,
    PolicyAlignmentResult,
)
from app.schemas.policy_findings import GeneratedPolicyFinding, PolicyEvidence

logger = logging.getLogger(__name__)


class JobDescriptionEnhancementState(TypedDict):
    job_id: UUID
    persist: bool
    evaluation_attempt_count: int
    enhancement_attempt_count: int
    validation_errors: list[str]
    job: NotRequired[Job]
    requirements: NotRequired[JobDescriptionGenerationRequest]
    evidence: NotRequired[list[PolicyEvidence]]
    current_policy_result: NotRequired[PolicyAlignmentResult]
    actionable_findings: NotRequired[list[GeneratedPolicyFinding]]
    enhancement_result: NotRequired[JobDescriptionGenerationResult]
    enhanced_description: NotRequired[GeneratedJobDescription]
    enhanced_content: NotRequired[str]
    enhanced_policy_result: NotRequired[PolicyAlignmentResult]
    error: RecrUnionError | None


class JobDescriptionEnhancementGraph:
    """Improve a generated JD from fresh, traceable policy findings."""

    def __init__(
        self,
        repository: JobRepository,
        llm_adapter: LLMAdapter,
        policy_repository: PolicyKnowledgeRepository,
        review_repository: PolicyReviewRepository,
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
        self._review_repository = review_repository
        self._embedding_adapter = embedding_adapter
        self._max_attempts = max_attempts
        self._retrieval_candidate_count = retrieval_candidate_count
        self._retrieval_top_k = retrieval_top_k
        self._retrieval_min_similarity = retrieval_min_similarity
        self._graph = self._build_graph()

    async def run(self, job_id: UUID, *, persist: bool = True) -> JobDescriptionEnhancementState:
        return await self._graph.ainvoke(
            {
                "job_id": job_id,
                "persist": persist,
                "evaluation_attempt_count": 0,
                "enhancement_attempt_count": 0,
                "validation_errors": [],
                "error": None,
            }
        )

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(JobDescriptionEnhancementState)
        builder.add_node("load_current_description", self._load_current_description)
        builder.add_node("retrieve_policy_context", self._retrieve_policy_context)
        builder.add_node("evaluate_current_alignment", self._evaluate_current_alignment)
        builder.add_node("select_actionable_findings", self._select_actionable_findings)
        builder.add_node("enhance_description", self._enhance_description)
        builder.add_node("validate_enhanced_description", self._validate_enhanced_description)
        builder.add_node("evaluate_enhanced_alignment", self._evaluate_enhanced_alignment)
        builder.add_node("validate_enhanced_findings", self._validate_enhanced_findings)
        builder.add_node("persist_enhanced_description", self._persist_enhanced_description)
        builder.add_edge(START, "load_current_description")
        builder.add_conditional_edges(
            "load_current_description",
            self._route_after_validation,
            {"continue": "retrieve_policy_context", "fail": END},
        )
        builder.add_conditional_edges(
            "retrieve_policy_context",
            self._route_after_validation,
            {"continue": "evaluate_current_alignment", "fail": END},
        )
        builder.add_conditional_edges(
            "evaluate_current_alignment",
            self._route_after_provider_call,
            {
                "continue": "select_actionable_findings",
                "retry": "evaluate_current_alignment",
                "fail": END,
            },
        )
        builder.add_conditional_edges(
            "select_actionable_findings",
            self._route_after_validation,
            {"continue": "enhance_description", "fail": END},
        )
        builder.add_conditional_edges(
            "enhance_description",
            self._route_after_enhancement,
            {
                "continue": "validate_enhanced_description",
                "retry": "enhance_description",
                "fail": END,
            },
        )
        builder.add_conditional_edges(
            "validate_enhanced_description",
            self._route_after_validation,
            {"continue": "evaluate_enhanced_alignment", "fail": END},
        )
        builder.add_conditional_edges(
            "evaluate_enhanced_alignment",
            self._route_after_provider_call,
            {
                "continue": "validate_enhanced_findings",
                "retry": "evaluate_enhanced_alignment",
                "fail": END,
            },
        )
        builder.add_conditional_edges(
            "validate_enhanced_findings",
            self._route_after_validation,
            {"continue": "persist_enhanced_description", "fail": END},
        )
        builder.add_edge("persist_enhanced_description", END)
        return builder.compile()

    def _load_current_description(
        self, state: JobDescriptionEnhancementState
    ) -> JobDescriptionEnhancementState:
        job = self._repository.get(state["job_id"])
        if job is None:
            return {**state, "error": JobNotFoundError(state["job_id"])}
        if job.status != JobStatus.GENERATED or not job.jd_content:
            return {
                **state,
                "error": InvalidJobStatusError("Only a GENERATED job description can be enhanced."),
            }
        return {
            **state,
            "job": job,
            "requirements": requirements_from_job(job),
            "error": None,
        }

    async def _retrieve_policy_context(
        self, state: JobDescriptionEnhancementState
    ) -> JobDescriptionEnhancementState:
        try:
            if not self._policy_repository.has_ready_documents(self._embedding_adapter.model_name):
                return {
                    **state,
                    "error": PolicyEnhancementUnavailableError(
                        "No READY company documents are available for enhancement."
                    ),
                }
            query_embedding = await asyncio.to_thread(
                self._embedding_adapter.embed_query,
                build_policy_retrieval_query(state["requirements"]),
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
                "Company-policy retrieval failed during enhancement",
                extra={"job_id": str(state["job_id"]), "operation": "policy_enhancement"},
            )
            return {**state, "error": PolicyRetrievalError("Company-policy retrieval failed.")}
        if not evidence:
            return {
                **state,
                "error": PolicyEnhancementUnavailableError(
                    "No relevant company-policy evidence was found for this job."
                ),
            }
        requirements = state["requirements"].model_copy(update={"policy_evidence": evidence})
        return {**state, "requirements": requirements, "evidence": evidence, "error": None}

    async def _evaluate_current_alignment(
        self, state: JobDescriptionEnhancementState
    ) -> JobDescriptionEnhancementState:
        return await self._evaluate_alignment(state, state["job"].jd_content or "", False)

    async def _evaluate_enhanced_alignment(
        self, state: JobDescriptionEnhancementState
    ) -> JobDescriptionEnhancementState:
        return await self._evaluate_alignment(state, state["enhanced_content"], True)

    async def _evaluate_alignment(
        self,
        state: JobDescriptionEnhancementState,
        content: str,
        enhanced: bool,
    ) -> JobDescriptionEnhancementState:
        attempt_count = state["evaluation_attempt_count"] + 1
        try:
            result = await self._llm_adapter.evaluate_policy_alignment(
                PolicyAlignmentRequest(
                    job_id=state["job_id"],
                    content=content,
                    requirements=state["requirements"],
                    evidence=state["evidence"],
                )
            )
        except LLMProviderError as error:
            return {**state, "evaluation_attempt_count": attempt_count, "error": error}
        key = "enhanced_policy_result" if enhanced else "current_policy_result"
        return {
            **state,
            "evaluation_attempt_count": attempt_count,
            key: result,
            "error": None,
        }

    @staticmethod
    def _select_actionable_findings(
        state: JobDescriptionEnhancementState,
    ) -> JobDescriptionEnhancementState:
        result = state["current_policy_result"]
        validation_error = _validate_evidence_references(state["evidence"], result)
        if validation_error is not None:
            return {**state, "error": validation_error}
        actionable = [
            finding
            for finding in result.evaluation.findings
            if finding.status
            in {PolicyAlignmentStatus.PARTIALLY_MET, PolicyAlignmentStatus.NOT_MET}
        ]
        if not actionable:
            return {
                **state,
                "error": PolicyEnhancementUnavailableError(
                    "No partially met or unmet policy findings require enhancement."
                ),
            }
        return {
            **state,
            "evaluation_attempt_count": 0,
            "actionable_findings": actionable,
            "error": None,
        }

    async def _enhance_description(
        self, state: JobDescriptionEnhancementState
    ) -> JobDescriptionEnhancementState:
        attempt_count = state["enhancement_attempt_count"] + 1
        try:
            result = await self._llm_adapter.enhance_job_description(
                JobDescriptionEnhancementRequest(
                    job_id=state["job_id"],
                    current_content=state["job"].jd_content or "",
                    requirements=state["requirements"],
                    evidence=state["evidence"],
                    findings=state["current_policy_result"].evaluation.findings,
                )
            )
        except LLMProviderError as error:
            return {**state, "enhancement_attempt_count": attempt_count, "error": error}
        return {
            **state,
            "enhancement_attempt_count": attempt_count,
            "enhancement_result": result,
            "enhanced_description": result.description,
            "error": None,
        }

    @staticmethod
    def _validate_enhanced_description(
        state: JobDescriptionEnhancementState,
    ) -> JobDescriptionEnhancementState:
        description = apply_authoritative_requirements(
            state["requirements"], state["enhanced_description"]
        )
        narrative = " ".join([description.summary, *description.responsibilities])
        if contains_protected_requirement(narrative):
            return {
                **state,
                "error": JobDescriptionValidationError(
                    "The enhanced job description did not pass validation."
                ),
            }
        return {
            **state,
            "enhanced_description": description,
            "enhanced_content": render_job_description(description),
            "error": None,
        }

    @staticmethod
    def _validate_enhanced_findings(
        state: JobDescriptionEnhancementState,
    ) -> JobDescriptionEnhancementState:
        error = _validate_evidence_references(state["evidence"], state["enhanced_policy_result"])
        return {**state, "error": error}

    def _persist_enhanced_description(
        self, state: JobDescriptionEnhancementState
    ) -> JobDescriptionEnhancementState:
        if not state["persist"]:
            return {**state, "error": None}
        job = state["job"]
        result = state["enhancement_result"]
        content = state["enhanced_content"]
        job.jd_content = content
        job.jd_generated_content = content
        job.jd_version += 1
        job.jd_generated_at = datetime.now(UTC)
        job.jd_provider = result.provider
        job.jd_model = result.model
        job.jd_generation_metadata = result.metadata.model_dump(exclude_none=True)
        review = build_policy_review(
            job,
            content,
            state["evidence"],
            state["enhanced_policy_result"],
            self._embedding_adapter.model_name,
        )
        self._review_repository.save(review)
        return {**state, "job": job, "error": None}

    @staticmethod
    def _route_after_validation(
        state: JobDescriptionEnhancementState,
    ) -> Literal["continue", "fail"]:
        return "fail" if state.get("error") is not None else "continue"

    def _route_after_provider_call(
        self, state: JobDescriptionEnhancementState
    ) -> Literal["continue", "retry", "fail"]:
        return self._provider_route(state, state["evaluation_attempt_count"])

    def _route_after_enhancement(
        self, state: JobDescriptionEnhancementState
    ) -> Literal["continue", "retry", "fail"]:
        return self._provider_route(state, state["enhancement_attempt_count"])

    def _provider_route(
        self, state: JobDescriptionEnhancementState, attempt_count: int
    ) -> Literal["continue", "retry", "fail"]:
        error = state.get("error")
        if error is None:
            return "continue"
        if (
            isinstance(error, LLMProviderError)
            and error.retryable
            and attempt_count < self._max_attempts
        ):
            return "retry"
        return "fail"


def _validate_evidence_references(
    evidence: list[PolicyEvidence],
    result: PolicyAlignmentResult,
) -> JobDescriptionValidationError | None:
    expected_ids = {item.evidence_id for item in evidence}
    finding_ids = [item.evidence_id for item in result.evaluation.findings]
    if len(finding_ids) != len(expected_ids) or set(finding_ids) != expected_ids:
        return JobDescriptionValidationError(
            "The policy-alignment result did not pass evidence validation."
        )
    return None
