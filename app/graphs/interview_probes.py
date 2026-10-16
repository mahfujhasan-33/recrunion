import re
from collections.abc import Callable
from typing import TYPE_CHECKING, NotRequired, TypedDict
from uuid import UUID, uuid4

from langgraph.graph import END, START, StateGraph

from app.adapters.llm import LLMAdapter
from app.errors import (
    InterviewAssistanceStateError,
    InterviewAssistanceValidationError,
    LLMProviderError,
    RecrUnionError,
)
from app.graphs.candidate_screening import contains_protected_screening_term
from app.models.interview_probes import (
    AssistanceKind,
    InterviewAssistanceRun,
    InterviewProbe,
    ProbeStatus,
)
from app.schemas.interview_probes import GeneratedProbeSet, ProbeGap, ProbeGenerationContext

if TYPE_CHECKING:
    from app.services.interview_probes import InterviewProbeService


class ProbeGraphState(TypedDict):
    run_id: UUID
    report: Callable[[int, str], None]
    attempts: int
    error: RecrUnionError | None
    run: NotRequired[InterviewAssistanceRun]
    context: NotRequired[ProbeGenerationContext]
    gaps: NotRequired[list[ProbeGap]]
    generated: NotRequired[GeneratedProbeSet]


class InterviewProbeGraph:
    """F11: persisted context -> deterministic gaps -> grounded wording -> validation -> storage."""

    def __init__(self, service: "InterviewProbeService", llm: LLMAdapter) -> None:
        self._service, self._llm = service, llm
        builder = StateGraph(ProbeGraphState)
        nodes = [
            ("load_selected_context", self._load),
            ("detect_evidence_gaps", self._detect),
            ("prioritize_evidence_gaps", self._prioritize),
            ("generate_probes", self._generate),
            ("validate_grounding_and_safety", self._validate),
            ("persist_probes", self._persist),
        ]
        for name, action in nodes:
            builder.add_node(name, action)
        builder.add_edge(START, nodes[0][0])
        for index, (name, _) in enumerate(nodes):
            next_name = nodes[index + 1][0] if index + 1 < len(nodes) else END
            if name == "generate_probes":
                builder.add_conditional_edges(
                    name,
                    self._after_generation,
                    {"retry": name, "fail": END, "continue": next_name},
                )
            else:
                builder.add_conditional_edges(
                    name,
                    lambda state: "fail" if state["error"] else "continue",
                    {"fail": END, "continue": next_name},
                )
        self._graph = builder.compile()

    async def run(self, run_id: UUID, report: Callable[[int, str], None]) -> ProbeGraphState:
        result = await self._graph.ainvoke(
            {"run_id": run_id, "report": report, "attempts": 0, "error": None}
        )
        if result["error"]:
            raise result["error"]
        return result

    def _load(self, state: ProbeGraphState) -> dict:
        state["report"](10, "Loading this interview's approved job and saved F1/F2 evidence")
        run = self._service.repository.get_run(state["run_id"])
        if run is None:
            return {
                "error": InterviewAssistanceStateError(
                    "Interview assistance is no longer available."
                )
            }
        try:
            context, fingerprint = self._service.context(
                run.interview_id, transcript=run.kind == AssistanceKind.FOLLOW_UP
            )
            if fingerprint != run.source_fingerprint:
                raise InterviewAssistanceStateError(
                    "Source evidence changed. Request fresh interview assistance."
                )
            context.request_text = run.request_text
            if (
                run.request_text
                and re.search(r"\b(?:answer|said|transcript|response)\b", run.request_text, re.I)
                and not context.transcript
            ):
                raise InterviewAssistanceStateError(
                    "No saved transcript is available yet. Wait for a final segment "
                    "or ask about a job requirement instead."
                )
            return {"run": run, "context": context}
        except RecrUnionError as error:
            return {"error": error}

    def _detect(self, state: ProbeGraphState) -> dict:
        state["report"](25, "Identifying missing, partial, and verification evidence")
        requirements = state["context"].gaps
        weak = [gap for gap in requirements if gap.match_status != "MET" or not gap.cv_evidence_ids]
        # Reserve scarce preparation questions for actual gaps when enough exist.
        # Runtime follow-ups may also explore a strongly evidenced requested subject.
        if state["run"].kind == AssistanceKind.FOLLOW_UP:
            return {"gaps": requirements}
        return {"gaps": weak if len(weak) >= 3 else requirements}

    def _prioritize(self, state: ProbeGraphState) -> dict:
        from app.services.interview_probes import prioritize_gaps

        state["report"](40, "Prioritizing evidence gaps for this application")
        selected = prioritize_gaps(state["gaps"])
        if state["run"].kind == AssistanceKind.PROBES:
            selected = selected[:5]
        context = state["context"].model_copy(update={"gaps": selected})
        return {"context": context, "gaps": selected}

    async def _generate(self, state: ProbeGraphState) -> dict:
        state["report"](55, "Drafting grounded questions and expected evidence")
        try:
            result = await self._llm.generate_interview_probes(state["context"])
            return {"generated": result, "attempts": state["attempts"] + 1, "error": None}
        except LLMProviderError as error:
            return {"error": error, "attempts": state["attempts"] + 1}

    @staticmethod
    def _after_generation(state: ProbeGraphState) -> str:
        if state["error"]:
            return (
                "retry"
                if state["attempts"] < 2 and getattr(state["error"], "retryable", True)
                else "fail"
            )
        return "continue"

    def _validate(self, state: ProbeGraphState) -> dict:
        state["report"](80, "Checking source ownership, job relevance, and question safety")
        try:
            validate_probes(
                state["generated"],
                state["context"],
                follow_up=state["run"].kind == AssistanceKind.FOLLOW_UP,
            )
            return {}
        except InterviewAssistanceValidationError as error:
            return {"error": error}

    def _persist(self, state: ProbeGraphState) -> dict:
        state["report"](92, "Saving suggestions without changing interview or candidate scores")
        # Recheck source freshness after the provider call before writing derived output.
        run = state["run"]
        _, fingerprint = self._service.context(run.interview_id)
        if fingerprint != run.source_fingerprint:
            return {
                "error": InterviewAssistanceStateError(
                    "Source evidence changed; suggestions were not saved."
                )
            }
        by_id = {gap.requirement_id: gap for gap in state["gaps"]}
        probes = [
            InterviewProbe(
                id=uuid4(),
                run_id=run.id,
                application_id=state["context"].application_id,
                requirement_id=item.requirement_id,
                requirement_text=by_id[item.requirement_id].requirement_text,
                question=item.question,
                reason=by_id[item.requirement_id].reason,
                expected_evidence=item.expected_evidence,
                evidence_refs=by_id[item.requirement_id].model_dump(mode="json"),
                priority=index + 1,
                status=ProbeStatus.READY,
            )
            for index, item in enumerate(state["generated"].probes)
        ]
        response = (
            {
                **state["generated"].probes[0].model_dump(mode="json"),
                "evidence_refs": {
                    **by_id[state["generated"].probes[0].requirement_id].model_dump(mode="json"),
                    "transcript": [
                        {**segment, "text": segment["text"][:350]}
                        for segment in state["context"].transcript
                        if segment["id"]
                        in {
                            str(item)
                            for item in state["generated"].probes[0].transcript_segment_ids
                        }
                    ],
                },
            }
            if run.kind == AssistanceKind.FOLLOW_UP
            else None
        )
        self._service.repository.complete(run, probes, response)
        return {}


def ensure_safe_text(text: str) -> None:
    if contains_protected_screening_term(text) or re.search(
        r"\b(?:pregnan\w*|marri\w*|children|family planning|medical|disabil\w*|"
        r"sexual\w*|race|ethnic\w*|religio\w*|gender|age|accent|nationality|"
        r"hiring recommendation|hire|reject candidate|candidate score|"
        r"confidence score|relevance score|articulation score|coverage classification|"
        r"accuracy score|completeness score|speaking clarity|birthday|political affiliation)\b",
        text,
        re.I,
    ):
        raise InterviewAssistanceValidationError(
            "Only job-related interview questions and evidence guidance are allowed. "
            "No personal-trait questions, scoring, or hiring recommendations."
        )


def validate_probes(
    result: GeneratedProbeSet, context: ProbeGenerationContext, *, follow_up: bool
) -> None:
    expected = 1 if follow_up else min(5, len(context.gaps))
    if len(result.probes) != expected:
        raise InterviewAssistanceValidationError(
            "The provider returned an incomplete question set. Please retry."
        )
    allowed = {gap.requirement_id: gap for gap in context.gaps}
    segment_ids = {item["id"] for item in context.transcript}
    seen: set[str] = set()
    requirements: set[UUID] = set()
    for probe in result.probes:
        ensure_safe_text(probe.model_dump_json())
        gap = allowed.get(probe.requirement_id)
        if gap is None or any(
            str(item) not in segment_ids for item in probe.transcript_segment_ids
        ):
            raise InterviewAssistanceValidationError(
                "The suggested question referenced evidence outside this interview."
            )
        if not follow_up and probe.requirement_id in requirements:
            raise InterviewAssistanceValidationError(
                "Questions must cover the selected distinct requirements."
            )
        requirements.add(probe.requirement_id)
        tokens = set(re.findall(r"[a-z0-9+#.]{2,}", gap.requirement_text.casefold())) - {
            "and",
            "the",
            "with",
            "experience",
            "years",
            "minimum",
            "required",
            "ability",
            "knowledge",
            "of",
            "in",
            "to",
        }
        if tokens and not any(token in probe.question.casefold() for token in tokens):
            raise InterviewAssistanceValidationError(
                "The suggested question was not grounded in its job requirement."
            )
        key = " ".join(probe.question.casefold().split())
        if key in seen or any(
            not item.strip() or len(item) > 600 for item in probe.expected_evidence
        ):
            raise InterviewAssistanceValidationError(
                "The provider returned duplicate or invalid question guidance."
            )
        seen.add(key)
    if (
        follow_up
        and context.transcript
        and context.request_text
        and re.search(r"\b(?:answer|said|transcript|response)\b", context.request_text, re.I)
        and not result.probes[0].transcript_segment_ids
    ):
        raise InterviewAssistanceValidationError(
            "A transcript-based follow-up must cite a saved segment from this interview."
        )
