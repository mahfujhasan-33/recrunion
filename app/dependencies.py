from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters.bluesky import BlueskyPublisher
from app.adapters.document_parser import CompanyDocumentParser
from app.adapters.document_storage import DocumentStorage, LocalDocumentStorage
from app.adapters.embeddings import EmbeddingAdapter
from app.adapters.gemini import GeminiLLMAdapter
from app.adapters.llm import LLMAdapter
from app.adapters.ollama_embeddings import OllamaEmbeddingAdapter
from app.adapters.publisher import PublisherAdapter
from app.config import get_settings
from app.database import get_db_session
from app.errors import EmbeddingProviderError, LLMConfigurationError
from app.graphs.candidate_screening import CandidateScreeningGraph
from app.graphs.job_description import JobDescriptionGraph
from app.graphs.job_description_enhancement import JobDescriptionEnhancementGraph
from app.graphs.recruiter_assistant import RecruiterAssistantGraph
from app.repositories.applications import ApplicationRepository
from app.repositories.assistant import AssistantRepository
from app.repositories.candidate_processing import CandidateProcessingRepository
from app.repositories.candidate_screening import CandidateScreeningRepository
from app.repositories.company_documents import CompanyDocumentRepository
from app.repositories.job_publications import JobPublicationRepository
from app.repositories.jobs import JobRepository
from app.repositories.policy_knowledge import PolicyKnowledgeRepository, PolicyReviewRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.services.application_intake import ApplicationIntakeService
from app.services.assistant import AssistantService
from app.services.candidate_pdf_extraction import CandidatePDFExtractor
from app.services.candidate_processing import CandidateProcessingService
from app.services.candidate_screening import CandidateScreeningService
from app.services.company_documents import CompanyDocumentService
from app.services.job_descriptions import JobDescriptionService
from app.services.job_publication_content import JobPublicationContentBuilder
from app.services.job_publications import JobPublishingService
from app.services.jobs import JobService
from app.services.policy_reviews import PolicyReviewService
from app.services.processing_jobs import ProcessingJobService


def get_job_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> JobService:
    """Provide a job service using the request-scoped database session."""

    return JobService(JobRepository(session))


def get_application_document_storage() -> DocumentStorage:
    settings = get_settings()
    return LocalDocumentStorage(
        settings.applications_root,
        settings.max_cv_size_mb * 1024 * 1024,
    )


def get_application_intake_service(
    session: Annotated[Session, Depends(get_db_session)],
    storage: Annotated[DocumentStorage, Depends(get_application_document_storage)],
) -> ApplicationIntakeService:
    settings = get_settings()
    return ApplicationIntakeService(
        JobRepository(session),
        ApplicationRepository(session),
        storage,
        max_file_size_bytes=settings.max_cv_size_mb * 1024 * 1024,
    )


def build_candidate_processing_service(
    session: Session,
    llm_adapter: LLMAdapter,
    embedding_adapter: EmbeddingAdapter,
    storage: DocumentStorage | None = None,
) -> CandidateProcessingService:
    settings = get_settings()
    return CandidateProcessingService(
        JobRepository(session),
        CandidateProcessingRepository(session),
        storage or get_application_document_storage(),
        CandidatePDFExtractor(),
        llm_adapter,
        embedding_adapter,
        worker_max_attempts=settings.worker_max_attempts,
        minimum_text_characters=settings.minimum_cv_text_characters,
    )


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


def get_embedding_adapter() -> EmbeddingAdapter:
    settings = get_settings()
    if settings.embedding_provider.casefold() != "ollama":
        raise EmbeddingProviderError("The configured embedding provider is not supported.")
    return OllamaEmbeddingAdapter(
        base_url=settings.ollama_base_url,
        model=settings.embedding_model,
        dimension=settings.embedding_dimension,
        timeout_seconds=settings.ollama_timeout_seconds,
    )


def get_candidate_processing_service(
    session: Annotated[Session, Depends(get_db_session)],
    llm_adapter: Annotated[LLMAdapter, Depends(get_llm_adapter)],
    embedding_adapter: Annotated[EmbeddingAdapter, Depends(get_embedding_adapter)],
    storage: Annotated[DocumentStorage, Depends(get_application_document_storage)],
) -> CandidateProcessingService:
    return build_candidate_processing_service(
        session,
        llm_adapter,
        embedding_adapter,
        storage,
    )


def build_candidate_screening_service(
    session: Session,
    llm_adapter: LLMAdapter,
    embedding_adapter: EmbeddingAdapter,
) -> CandidateScreeningService:
    settings = get_settings()
    screening_repository = CandidateScreeningRepository(session)
    graph = CandidateScreeningGraph(
        screening_repository,
        CandidateProcessingRepository(session),
        llm_adapter,
        embedding_adapter,
        max_attempts=settings.gemini_max_retries + 1,
        retrieval_top_k=settings.screening_retrieval_top_k,
        retrieval_min_similarity=settings.screening_retrieval_min_similarity,
    )
    return CandidateScreeningService(
        JobRepository(session),
        screening_repository,
        graph,
        worker_max_attempts=settings.worker_max_attempts,
    )


def get_candidate_screening_service(
    session: Annotated[Session, Depends(get_db_session)],
    llm_adapter: Annotated[LLMAdapter, Depends(get_llm_adapter)],
    embedding_adapter: Annotated[EmbeddingAdapter, Depends(get_embedding_adapter)],
) -> CandidateScreeningService:
    return build_candidate_screening_service(session, llm_adapter, embedding_adapter)


def get_publisher_adapter() -> PublisherAdapter:
    settings = get_settings()
    return BlueskyPublisher(
        identifier=settings.bluesky_identifier,
        app_password=settings.bluesky_app_password,
        service_url=settings.bluesky_service_url,
        timeout_seconds=settings.bluesky_timeout_seconds,
    )


def build_job_publishing_service(
    session: Session,
    publisher_adapter: PublisherAdapter,
) -> JobPublishingService:
    return JobPublishingService(
        JobPublicationRepository(session),
        publisher_adapter,
        JobPublicationContentBuilder(),
    )


def get_job_publishing_service(
    session: Annotated[Session, Depends(get_db_session)],
    publisher_adapter: Annotated[PublisherAdapter, Depends(get_publisher_adapter)],
) -> JobPublishingService:
    return build_job_publishing_service(session, publisher_adapter)


def get_job_description_service(
    session: Annotated[Session, Depends(get_db_session)],
    llm_adapter: Annotated[LLMAdapter, Depends(get_llm_adapter)],
    embedding_adapter: Annotated[EmbeddingAdapter, Depends(get_embedding_adapter)],
) -> JobDescriptionService:
    """Provide the M2 job-description service and graph."""

    return build_job_description_service(session, llm_adapter, embedding_adapter)


def build_job_description_service(
    session: Session,
    llm_adapter: LLMAdapter,
    embedding_adapter: EmbeddingAdapter,
) -> JobDescriptionService:
    repository = JobRepository(session)
    policy_repository = PolicyKnowledgeRepository(session)
    review_repository = PolicyReviewRepository(session)
    graph = JobDescriptionGraph(
        repository,
        llm_adapter,
        policy_repository,
        review_repository,
        embedding_adapter,
        max_attempts=get_settings().gemini_max_retries + 1,
        retrieval_candidate_count=get_settings().policy_retrieval_candidate_count,
        retrieval_top_k=get_settings().policy_retrieval_top_k,
        retrieval_min_similarity=get_settings().policy_retrieval_min_similarity,
    )
    enhancement_graph = JobDescriptionEnhancementGraph(
        repository,
        llm_adapter,
        policy_repository,
        review_repository,
        embedding_adapter,
        max_attempts=get_settings().gemini_max_retries + 1,
        retrieval_candidate_count=get_settings().policy_retrieval_candidate_count,
        retrieval_top_k=get_settings().policy_retrieval_top_k,
        retrieval_min_similarity=get_settings().policy_retrieval_min_similarity,
    )
    return JobDescriptionService(repository, graph, enhancement_graph, review_repository)


def get_company_document_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> CompanyDocumentService:
    settings = get_settings()
    return CompanyDocumentService(
        CompanyDocumentRepository(session),
        ProcessingJobRepository(session),
        LocalDocumentStorage(
            settings.company_documents_root,
            settings.max_company_document_size_mb * 1024 * 1024,
        ),
        CompanyDocumentParser(),
        worker_max_attempts=settings.worker_max_attempts,
    )


def get_processing_job_service(
    session: Annotated[Session, Depends(get_db_session)],
) -> ProcessingJobService:
    return ProcessingJobService(ProcessingJobRepository(session))


def get_policy_review_service(
    session: Annotated[Session, Depends(get_db_session)],
    llm_adapter: Annotated[LLMAdapter, Depends(get_llm_adapter)],
    embedding_adapter: Annotated[EmbeddingAdapter, Depends(get_embedding_adapter)],
) -> PolicyReviewService:
    return build_policy_review_service(session, llm_adapter, embedding_adapter)


def build_policy_review_service(
    session: Session,
    llm_adapter: LLMAdapter,
    embedding_adapter: EmbeddingAdapter,
) -> PolicyReviewService:
    settings = get_settings()
    return PolicyReviewService(
        JobRepository(session),
        PolicyKnowledgeRepository(session),
        PolicyReviewRepository(session),
        CompanyDocumentRepository(session),
        embedding_adapter,
        llm_adapter,
        candidate_count=settings.policy_retrieval_candidate_count,
        top_k=settings.policy_retrieval_top_k,
        minimum_similarity=settings.policy_retrieval_min_similarity,
    )


def build_assistant_service(
    session: Session,
    llm_adapter: LLMAdapter,
    embedding_adapter: EmbeddingAdapter,
    publisher_adapter: PublisherAdapter | None = None,
) -> AssistantService:
    settings = get_settings()
    return AssistantService(
        AssistantRepository(session),
        ProcessingJobRepository(session),
        JobService(JobRepository(session)),
        build_job_description_service(session, llm_adapter, embedding_adapter),
        build_policy_review_service(session, llm_adapter, embedding_adapter),
        build_job_publishing_service(
            session,
            publisher_adapter or get_publisher_adapter(),
        ),
        RecruiterAssistantGraph(
            llm_adapter,
            max_attempts=settings.gemini_max_retries + 1,
        ),
        worker_max_attempts=settings.worker_max_attempts,
    )


def get_assistant_service(
    session: Annotated[Session, Depends(get_db_session)],
    llm_adapter: Annotated[LLMAdapter, Depends(get_llm_adapter)],
    embedding_adapter: Annotated[EmbeddingAdapter, Depends(get_embedding_adapter)],
    publisher_adapter: Annotated[PublisherAdapter, Depends(get_publisher_adapter)],
) -> AssistantService:
    return build_assistant_service(
        session,
        llm_adapter,
        embedding_adapter,
        publisher_adapter,
    )
