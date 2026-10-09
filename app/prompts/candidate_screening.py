import json

from langchain_core.messages import HumanMessage, SystemMessage

from app.schemas.screening import CandidateScreeningEvaluationRequest


def build_candidate_screening_prompt(
    request: CandidateScreeningEvaluationRequest,
) -> list[SystemMessage | HumanMessage]:
    """Build an evidence-only F1 screening request without hiring decisions."""

    return [
        SystemMessage(
            content=(
                "Evaluate every supplied job requirement using only the supplied processed-CV "
                "profile and retrieved evidence. Return exactly one match per requirement ID. "
                "MET requires sufficient direct evidence. PARTIALLY_MET requires relevant but "
                "incomplete, weak, ambiguous, or below-threshold evidence. UNMET means the "
                "submitted CV does not sufficiently demonstrate the requirement; it does not "
                "mean the candidate lacks the capability. MET and PARTIALLY_MET must reference "
                "one or more evidence IDs assigned to that requirement. UNMET may use no "
                "evidence. Never invent IDs, infer protected characteristics, rank candidates, "
                "make a hiring decision, or expose hidden reasoning. Keep each justification "
                "concise and recruiter-facing."
            )
        ),
        HumanMessage(content=json.dumps(request.model_dump(mode="json"), ensure_ascii=False)),
    ]
