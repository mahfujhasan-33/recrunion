from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters.gemini import GeminiLLMAdapter
from app.adapters.llm import LLMAdapter
from app.config import get_settings
from app.database import get_db_session
from app.errors import LLMConfigurationError
from app.graphs.job_description import JobDescriptionGraph
from app.repositories.jobs import JobRepository
from app.services.job_descriptions import JobDescriptionService
from app.services.jobs import JobService


def get_job_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> JobService:
    """Provide a job service using the request-scoped database session."""

    return JobService(JobRepository(session))


def get_llm_adapter() -> LLMAdapter:
    """Provide the configured LLM adapter without exposing provider details."""

    settings = get_settings()
    if settings.llm_provider.casefold() != "gemini":
        raise LLMConfigurationError("The configured AI provider is not supported.")
    return GeminiLLMAdapter(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout_seconds=settings.gemini_timeout_seconds,
    )


def get_job_description_service(
    session: Annotated[Session, Depends(get_db_session)],
    llm_adapter: Annotated[LLMAdapter, Depends(get_llm_adapter)],
) -> JobDescriptionService:
    """Provide the M2 job-description service and graph."""

    repository = JobRepository(session)
    graph = JobDescriptionGraph(
        repository,
        llm_adapter,
        max_attempts=get_settings().gemini_max_retries + 1,
    )
    return JobDescriptionService(repository, graph)
