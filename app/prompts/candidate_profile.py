from langchain_core.messages import HumanMessage, SystemMessage

from app.schemas.candidate_profiles import CandidateProfileExtractionRequest


def build_candidate_profile_prompt(
    request: CandidateProfileExtractionRequest,
) -> list[SystemMessage | HumanMessage]:
    """Build a constrained, evidence-first CV profile extraction prompt."""

    evidence = "\n\n".join(
        (f"Evidence ID: {item.evidence_id}\nPage: {item.page_number}\nText: {item.content}")
        for item in request.evidence
    )
    return [
        SystemMessage(
            content=(
                "Extract only explicitly supported candidate information from the supplied CV "
                "evidence. Every returned item must cite one or more supplied evidence IDs. "
                "Do not infer missing information. Do not extract or mention race, ethnicity, "
                "religion, gender, marital status, disability, political affiliation, age, or "
                "other protected characteristics. Contact details are for record identity only. "
                "Return empty lists for categories that are not supported by the evidence."
            )
        ),
        HumanMessage(
            content=(
                f"Candidate reference: {request.candidate_reference}\n"
                f"Source document ID: {request.document_id}\n\n"
                "Create the structured candidate profile from these evidence chunks:\n\n"
                f"{evidence}"
            )
        ),
    ]
