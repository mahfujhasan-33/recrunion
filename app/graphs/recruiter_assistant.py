from typing import Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from app.adapters.llm import LLMAdapter
from app.errors import LLMProviderError, RecrUnionError
from app.schemas.assistant import AssistantTurnPlan, AssistantTurnRequest


class RecruiterAssistantState(TypedDict):
    request: AssistantTurnRequest
    attempt_count: int
    plan: NotRequired[AssistantTurnPlan]
    error: RecrUnionError | None


class RecruiterAssistantGraph:
    """Interpret recruiter intent with bounded retries and an explicit policy guard."""

    def __init__(self, llm_adapter: LLMAdapter, *, max_attempts: int = 2) -> None:
        self._llm_adapter = llm_adapter
        self._max_attempts = max_attempts
        self._graph = self._build_graph()

    async def run(self, request: AssistantTurnRequest) -> RecruiterAssistantState:
        return await self._graph.ainvoke({"request": request, "attempt_count": 0, "error": None})

    def _build_graph(self):  # type: ignore[no-untyped-def]
        builder = StateGraph(RecruiterAssistantState)
        builder.add_node("interpret_recruiter_intent", self._interpret_recruiter_intent)
        builder.add_node("enforce_action_boundary", self._enforce_action_boundary)
        builder.add_edge(START, "interpret_recruiter_intent")
        builder.add_conditional_edges(
            "interpret_recruiter_intent",
            self._route_after_provider,
            {
                "continue": "enforce_action_boundary",
                "retry": "interpret_recruiter_intent",
                "fail": END,
            },
        )
        builder.add_edge("enforce_action_boundary", END)
        return builder.compile()

    async def _interpret_recruiter_intent(
        self, state: RecruiterAssistantState
    ) -> RecruiterAssistantState:
        attempt_count = state["attempt_count"] + 1
        try:
            plan = await self._llm_adapter.plan_assistant_turn(state["request"])
        except LLMProviderError as error:
            return {**state, "attempt_count": attempt_count, "error": error}
        return {**state, "attempt_count": attempt_count, "plan": plan, "error": None}

    @staticmethod
    def _enforce_action_boundary(
        state: RecruiterAssistantState,
    ) -> RecruiterAssistantState:
        # The structured enum prevents arbitrary tools. Consequential approval and
        # publication remain confirmation requests and are never executed by this graph.
        return {**state, "error": None}

    def _route_after_provider(
        self, state: RecruiterAssistantState
    ) -> Literal["continue", "retry", "fail"]:
        error = state.get("error")
        if error is None:
            return "continue"
        if error.retryable and state["attempt_count"] < self._max_attempts:
            return "retry"
        return "fail"
