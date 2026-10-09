import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, NotRequired, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph

from app.adapters.embeddings import EmbeddingAdapter
from app.adapters.llm import LLMAdapter
from app.errors import (
    CandidateScreeningError,
    CandidateScreeningEvidenceError,
    CandidateScreeningNotFoundError,
    CandidateScreeningStateError,
    CandidateScreeningValidationError,
    LLMProviderError,
    RecrUnionError,
)
from app.graphs.job_description import PROTECTED_REQUIREMENT_TERMS
from app.models.applications import CandidateDocumentProcessingStatus
from app.models.jobs import JobRequirement, RequirementType
from app.models.screening import (
    CandidateRequirementEvidence,
    CandidateRequirementMatch,
    RequirementMatchStatus,
)
from app.repositories.candidate_processing import (
    CandidateEvidenceMatch,
    CandidateProcessingRepository,
)
from app.repositories.candidate_screening import (
    CandidateScreeningContext,
    CandidateScreeningRepository,
)
from app.schemas.candidate_profiles import CandidateProfileData
from app.schemas.screening import (
    CandidateScreeningEvaluationRequest,
    CandidateScreeningEvaluationResult,
    GeneratedRequirementMatch,
    ScreeningEvidence,
    ScreeningRequirement,
    ScreeningSummary,
)

ProgressReporter = Callable[[int, str], None]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidatedRequirementMatch:
    requirement: ScreeningRequirement
    generated: GeneratedRequirementMatch
    evidence: list[ScreeningEvidence]


class CandidateScreeningState(TypedDict):
    screening_id: UUID
    evaluation_attempt_count: int
    validation_errors: list[str]
    report: ProgressReporter
    context: NotRequired[CandidateScreeningContext]
    requirements: NotRequired[list[ScreeningRequirement]]
    evidence: NotRequired[list[ScreeningEvidence]]
    evaluation_result: NotRequired[CandidateScreeningEvaluationResult]
    validated_matches: NotRequired[list[ValidatedRequirementMatch]]
    required_summary: NotRequired[ScreeningSummary]
    preferred_summary: NotRequired[ScreeningSummary]
    error: RecrUnionError | None


class CandidateScreeningGraph:
    """Evaluate persisted CV evidence against every current job requirement."""

    def __init__(
        self,
        screening_repository: CandidateScreeningRepository,
        candidate_repository: CandidateProcessingRepository,
        llm_adapter: LLMAdapter,
        embedding_adapter: EmbeddingAdapter,
        *,
        max_attempts: int,
        retrieval_top_k: int,
        retrieval_min_similarity: float,
    ) -> None:
        self._screening_repository = screening_repository
        self._candidate_repository = candidate_repository
        self._llm_adapter = llm_adapter
        self._embedding_adapter = embedding_adapter
        self._max_attempts = max_attempts
        self._retrieval_top_k = retrieval_top_k
        self._retrieval_min_similarity = retrieval_min_similarity
        self._graph = self._build_graph()

    async def run(
        self,
        screening_id: UUID,
        report: ProgressReporter,
    ) -> CandidateScreeningState:
        return await self._graph.ainvoke(
            {
                "screening_id": screening_id,
                "evaluation_attempt_count": 0,
                "validation_errors": [],
                "report": report,
                "error": None,
            }
        )

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(CandidateScreeningState)
        builder.add_node("load_screening_context", self._load_screening_context)
        builder.add_node("retrieve_requirement_evidence", self._retrieve_requirement_evidence)
        builder.add_node("evaluate_requirement_matches", self._evaluate_requirement_matches)
        builder.add_node("validate_match_evidence", self._validate_match_evidence)
        builder.add_node("build_screening_summary", self._build_screening_summary)
        builder.add_node("persist_screening", self._persist_screening)
        builder.add_edge(START, "load_screening_context")
        builder.add_conditional_edges(
            "load_screening_context",
            self._route_after_validation,
            {"continue": "retrieve_requirement_evidence", "fail": END},
        )
        builder.add_conditional_edges(
            "retrieve_requirement_evidence",
            self._route_after_validation,
            {"continue": "evaluate_requirement_matches", "fail": END},
        )
        builder.add_conditional_edges(
            "evaluate_requirement_matches",
            self._route_after_evaluation,
            {
                "continue": "validate_match_evidence",
                "retry": "evaluate_requirement_matches",
                "fail": END,
            },
        )
        builder.add_conditional_edges(
            "validate_match_evidence",
            self._route_after_validation,
            {"continue": "build_screening_summary", "fail": END},
        )
        builder.add_edge("build_screening_summary", "persist_screening")
        builder.add_edge("persist_screening", END)
        return builder.compile()

    def _load_screening_context(
        self,
        state: CandidateScreeningState,
    ) -> CandidateScreeningState:
        state["report"](12, "Loading the processed candidate and job requirements")
        context = self._screening_repository.get_context(state["screening_id"])
        if context is None:
            return {**state, "error": CandidateScreeningNotFoundError()}
        if context.document.processing_status != CandidateDocumentProcessingStatus.READY:
            return {
                **state,
                "error": CandidateScreeningStateError(
                    "Candidate screening requires a READY processed CV."
                ),
            }
        if (
            context.document.embedding_model != self._embedding_adapter.model_name
            or context.document.embedding_dimension != self._embedding_adapter.dimension
        ):
            return {
                **state,
                "error": CandidateScreeningStateError(
                    "Candidate evidence embeddings are not compatible with screening."
                ),
            }
        if not context.job.requirements:
            return {
                **state,
                "error": CandidateScreeningValidationError(
                    "The job does not contain screening requirements."
                ),
            }
        if any(
            contains_protected_screening_term(requirement.text)
            for requirement in context.job.requirements
        ):
            return {
                **state,
                "error": CandidateScreeningValidationError(
                    "Protected characteristics cannot be used for candidate screening."
                ),
            }
        fingerprint = build_requirements_fingerprint(context.job.requirements)
        if fingerprint != context.screening.requirements_fingerprint:
            return {
                **state,
                "error": CandidateScreeningStateError(
                    "Job requirements changed after screening was queued. Queue screening again."
                ),
            }
        requirements = [requirement_to_schema(item) for item in context.job.requirements]
        return {**state, "context": context, "requirements": requirements, "error": None}

    async def _retrieve_requirement_evidence(
        self,
        state: CandidateScreeningState,
    ) -> CandidateScreeningState:
        state["report"](32, "Retrieving candidate-specific evidence for each requirement")
        context = state["context"]
        evidence_by_id: dict[UUID, ScreeningEvidence] = {}
        requirements: list[ScreeningRequirement] = []
        try:
            for requirement in state["requirements"]:
                query_embedding = await asyncio.to_thread(
                    self._embedding_adapter.embed_query,
                    build_requirement_query(context.job.title, requirement),
                )
                retrieved = self._candidate_repository.retrieve_evidence(
                    context.application.id,
                    query_embedding,
                    embedding_model=self._embedding_adapter.model_name,
                    top_k=self._retrieval_top_k,
                )
                scoped = [
                    item
                    for item in retrieved
                    if item.document_id == context.document.id
                    and item.similarity >= self._retrieval_min_similarity
                ]
                evidence_ids: list[UUID] = []
                for item in scoped:
                    evidence = evidence_from_match(item)
                    evidence_by_id[evidence.evidence_id] = evidence
                    evidence_ids.append(evidence.evidence_id)
                requirements.append(
                    requirement.model_copy(update={"evidence_chunk_ids": evidence_ids})
                )
        except RecrUnionError as error:
            return {**state, "error": error}
        except Exception:
            logger.exception(
                "Candidate evidence retrieval failed",
                extra={
                    "screening_id": str(state["screening_id"]),
                    "operation": "candidate_screening_retrieval",
                },
            )
            return {
                **state,
                "error": CandidateScreeningError(
                    "Candidate evidence retrieval could not be completed."
                ),
            }
        return {
            **state,
            "requirements": requirements,
            "evidence": list(evidence_by_id.values()),
            "error": None,
        }

    async def _evaluate_requirement_matches(
        self,
        state: CandidateScreeningState,
    ) -> CandidateScreeningState:
        state["report"](55, "Evaluating job requirements against retrieved CV evidence")
        attempt_count = state["evaluation_attempt_count"] + 1
        context = state["context"]
        profile = CandidateProfileData.model_validate(context.profile.structured_json).model_copy(
            update={"contact_details": []}
        )
        try:
            result = await self._llm_adapter.evaluate_candidate_screening(
                CandidateScreeningEvaluationRequest(
                    job_title=context.job.title,
                    candidate_profile=profile,
                    requirements=state["requirements"],
                    evidence=state["evidence"],
                )
            )
        except LLMProviderError as error:
            return {**state, "evaluation_attempt_count": attempt_count, "error": error}
        return {
            **state,
            "evaluation_attempt_count": attempt_count,
            "evaluation_result": result,
            "error": None,
        }

    @staticmethod
    def _validate_match_evidence(
        state: CandidateScreeningState,
    ) -> CandidateScreeningState:
        state["report"](72, "Validating requirement and evidence references")
        requirements = {item.requirement_id: item for item in state["requirements"]}
        evidence = {item.evidence_id: item for item in state["evidence"]}
        generated = state["evaluation_result"].evaluation.matches
        generated_ids = [item.requirement_id for item in generated]
        errors: list[str] = []
        if len(generated_ids) != len(set(generated_ids)):
            errors.append("The screening result contains duplicate requirement IDs.")
        if set(generated_ids) != set(requirements):
            errors.append("The screening result must contain every current job requirement.")
        validated: list[ValidatedRequirementMatch] = []
        for item in generated:
            requirement = requirements.get(item.requirement_id)
            if requirement is None:
                continue
            evidence_ids = list(dict.fromkeys(item.evidence_chunk_ids))
            allowed = set(requirement.evidence_chunk_ids)
            if not set(evidence_ids).issubset(allowed):
                errors.append("The screening result contains an unsupported evidence reference.")
                continue
            if (
                item.status
                in {
                    RequirementMatchStatus.MET,
                    RequirementMatchStatus.PARTIALLY_MET,
                }
                and not evidence_ids
            ):
                errors.append("Positive or partial matches require supporting CV evidence.")
                continue
            selected = [evidence[evidence_id] for evidence_id in evidence_ids]
            if any(entry.document_id != state["context"].document.id for entry in selected):
                errors.append("Screening evidence belongs to a different candidate document.")
                continue
            generated_item = item
            if item.status == RequirementMatchStatus.UNMET:
                generated_item = item.model_copy(
                    update={"justification": neutral_unmet_justification(item.justification)}
                )
            validated.append(
                ValidatedRequirementMatch(
                    requirement=requirement,
                    generated=generated_item,
                    evidence=selected,
                )
            )
        if errors:
            return {
                **state,
                "validation_errors": sorted(set(errors)),
                "error": CandidateScreeningEvidenceError(
                    "The candidate screening result contains invalid evidence references."
                ),
            }
        return {
            **state,
            "validated_matches": validated,
            "validation_errors": [],
            "error": None,
        }

    @staticmethod
    def _build_screening_summary(
        state: CandidateScreeningState,
    ) -> CandidateScreeningState:
        state["report"](82, "Building deterministic screening outcome counts")
        required = summarize_matches(state["validated_matches"], RequirementType.REQUIRED)
        preferred = summarize_matches(state["validated_matches"], RequirementType.PREFERRED)
        return {
            **state,
            "required_summary": required,
            "preferred_summary": preferred,
            "error": None,
        }

    def _persist_screening(self, state: CandidateScreeningState) -> CandidateScreeningState:
        state["report"](92, "Persisting validated screening results")
        context = state["context"]
        evidence_by_id = {item.evidence_id: item for item in state["evidence"]}
        matches: list[CandidateRequirementMatch] = []
        for validated in state["validated_matches"]:
            match_id = uuid4()
            requirement = validated.requirement
            match = CandidateRequirementMatch(
                id=match_id,
                screening_id=context.screening.id,
                job_requirement_id=requirement.requirement_id,
                category=requirement.category,
                requirement_type=requirement.requirement_type,
                requirement_text=requirement.text,
                minimum_value=requirement.minimum_value,
                priority=requirement.priority,
                match_status=validated.generated.status,
                justification=validated.generated.justification,
                evidence=[
                    CandidateRequirementEvidence(
                        match_id=match_id,
                        candidate_cv_chunk_id=evidence.evidence_id,
                        retrieval_similarity=evidence_by_id[evidence.evidence_id].similarity,
                    )
                    for evidence in validated.evidence
                ],
            )
            matches.append(match)
        required = state["required_summary"]
        preferred = state["preferred_summary"]
        result = state["evaluation_result"]
        self._screening_repository.complete(
            context.screening,
            matches,
            required_met=required.met,
            required_partially_met=required.partially_met,
            required_unmet=required.unmet,
            preferred_met=preferred.met,
            preferred_partially_met=preferred.partially_met,
            preferred_unmet=preferred.unmet,
            provider=result.provider,
            model=result.model,
        )
        return {**state, "error": None}

    @staticmethod
    def _route_after_validation(
        state: CandidateScreeningState,
    ) -> Literal["continue", "fail"]:
        return "fail" if state.get("error") is not None else "continue"

    def _route_after_evaluation(
        self,
        state: CandidateScreeningState,
    ) -> Literal["continue", "retry", "fail"]:
        error = state.get("error")
        if error is None:
            return "continue"
        if (
            isinstance(error, LLMProviderError)
            and error.retryable
            and state["evaluation_attempt_count"] < self._max_attempts
        ):
            return "retry"
        return "fail"


def requirement_to_schema(requirement: JobRequirement) -> ScreeningRequirement:
    return ScreeningRequirement(
        requirement_id=requirement.id,
        category=requirement.category,
        requirement_type=requirement.requirement_type,
        text=requirement.text,
        minimum_value=requirement.minimum_value,
        priority=requirement.priority,
    )


def build_requirements_fingerprint(requirements: list[JobRequirement]) -> str:
    payload = [
        {
            "id": str(item.id),
            "category": item.category.value,
            "type": item.requirement_type.value,
            "text": item.text.strip(),
            "minimum": decimal_text(item.minimum_value),
            "priority": item.priority,
        }
        for item in sorted(requirements, key=lambda value: (value.priority, value.id.hex))
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_requirement_query(job_title: str, requirement: ScreeningRequirement) -> str:
    parts = [
        f"Role: {job_title}",
        f"Requirement category: {requirement.category.value}",
        f"Requirement priority: {requirement.requirement_type.value}",
        f"Requirement: {requirement.text}",
    ]
    if requirement.minimum_value is not None:
        parts.append(f"Minimum threshold: {requirement.minimum_value} years")
    parts.append("Candidate CV evidence that demonstrates this requirement")
    return "\n".join(parts)


def evidence_from_match(match: CandidateEvidenceMatch) -> ScreeningEvidence:
    return ScreeningEvidence(
        evidence_id=match.chunk_id,
        document_id=match.document_id,
        page_number=match.page_number,
        content=match.content,
        similarity=match.similarity,
    )


def summarize_matches(
    matches: list[ValidatedRequirementMatch],
    requirement_type: RequirementType,
) -> ScreeningSummary:
    statuses = [
        item.generated.status
        for item in matches
        if item.requirement.requirement_type == requirement_type
    ]
    return ScreeningSummary(
        met=statuses.count(RequirementMatchStatus.MET),
        partially_met=statuses.count(RequirementMatchStatus.PARTIALLY_MET),
        unmet=statuses.count(RequirementMatchStatus.UNMET),
    )


def neutral_unmet_justification(_provider_justification: str) -> str:
    return "The submitted CV does not contain sufficient evidence to demonstrate this requirement."


def decimal_text(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


def contains_protected_screening_term(text: str) -> bool:
    return any(
        re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE)
        for term in PROTECTED_REQUIREMENT_TERMS
    )
