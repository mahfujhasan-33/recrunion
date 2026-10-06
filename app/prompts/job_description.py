import json

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.schemas.job_descriptions import JobDescriptionGenerationRequest

SYSTEM_PROMPT = """You draft professional, recruiter-editable job descriptions.
Use only the supplied structured job requirements.
Never invent mandatory skills, qualifications, experience, or selection criteria.
Copy the supplied title, skills, qualifications, experience, and application email
exactly into their corresponding structured fields; do not paraphrase those values.
Keep required and preferred criteria clearly separated.
Do not introduce requirements based on age, gender, ethnicity, nationality, religion,
marital status, disability, pregnancy, or any other protected characteristic.
Retain the supplied application email in the application information.
Return only content that conforms to the requested structured schema."""


def build_job_description_prompt(
    request: JobDescriptionGenerationRequest,
) -> list[BaseMessage]:
    """Build provider messages without logging recruitment data."""

    requirements = request.model_dump(mode="json")
    requirements.pop("job_id")
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=(
                "Create a job description from these recruiter-approved requirements:\n"
                f"{json.dumps(requirements, ensure_ascii=False)}"
            )
        ),
    ]
