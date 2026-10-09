from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.adapters.document_storage import LocalDocumentStorage
from app.adapters.publisher import JobPublicationContent, PublicationResult
from app.database import Base, get_db_session
from app.dependencies import (
    get_application_document_storage,
    get_embedding_adapter,
    get_llm_adapter,
    get_publisher_adapter,
)
from app.errors import LLMProviderError, PublisherError
from app.main import create_app
from app.models.jobs import EmploymentType
from app.models.screening import RequirementMatchStatus
from app.schemas.assistant import (
    AssistantIntent,
    AssistantTurnPlan,
    AssistantTurnRequest,
    RequirementDraft,
)
from app.schemas.candidate_profiles import (
    CandidateCertification,
    CandidateContactDetail,
    CandidateEducation,
    CandidateProfileData,
    CandidateProfileExtractionRequest,
    CandidateProfileExtractionResult,
    CandidateProject,
    CandidateSkill,
    CandidateWorkHistory,
    ContactDetailKind,
)
from app.schemas.job_descriptions import (
    GeneratedJobDescription,
    JobDescriptionEnhancementRequest,
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
    LLMGenerationMetadata,
    PolicyAlignmentRequest,
    PolicyAlignmentResult,
)
from app.schemas.policy_findings import (
    GeneratedPolicyFinding,
    PolicyAlignmentEvaluation,
)
from app.schemas.screening import (
    CandidateScreeningEvaluation,
    CandidateScreeningEvaluationRequest,
    CandidateScreeningEvaluationResult,
    GeneratedRequirementMatch,
)


class FakeLLMAdapter:
    """Deterministic adapter used by graph, service, and API tests."""

    def __init__(self) -> None:
        self.call_count = 0
        self.policy_call_count = 0
        self.errors: list[LLMProviderError] = []
        self.policy_errors: list[LLMProviderError] = []
        self.enhancement_call_count = 0
        self.profile_call_count = 0
        self.profile_errors: list[LLMProviderError] = []
        self.profile_override: CandidateProfileData | None = None
        self.screening_call_count = 0
        self.screening_errors: list[LLMProviderError] = []
        self.screening_override: CandidateScreeningEvaluation | None = None

    async def plan_assistant_turn(
        self,
        request: AssistantTurnRequest,
    ) -> AssistantTurnPlan:
        message = request.user_message.casefold()
        if "retry" in message and "publish" in message:
            return AssistantTurnPlan(
                intent=AssistantIntent.RETRY_JOB_PUBLICATION,
                response_message="I will prepare an explicit publication retry.",
            )
        if "publish" in message:
            return AssistantTurnPlan(
                intent=AssistantIntent.PUBLISH_JOB,
                response_message="I will check whether this job can be published.",
            )
        if "publication status" in message:
            return AssistantTurnPlan(
                intent=AssistantIntent.GET_PUBLICATION_STATUS,
                response_message="I will check the publication status.",
            )
        if "generate" in message:
            return AssistantTurnPlan(
                intent=AssistantIntent.GENERATE_DESCRIPTION,
                response_message="I will generate the job description.",
            )
        if "enhance" in message:
            return AssistantTurnPlan(
                intent=AssistantIntent.ENHANCE_DESCRIPTION,
                response_message="I will prepare an enhancement proposal.",
            )
        if "recheck" in message or "policy" in message:
            return AssistantTurnPlan(
                intent=AssistantIntent.RECHECK_POLICY,
                response_message="I will recheck policy alignment.",
            )
        current = request.current_requirements or RequirementDraft()
        requirements = current.model_copy(
            update={
                "title": current.title or "AI/ML Engineer",
                "employment_type": current.employment_type or EmploymentType.FULL_TIME,
                "required_skills": current.required_skills or ["Python", "Machine learning"],
                "preferred_skills": current.preferred_skills or ["MLOps"],
                "qualifications": current.qualifications
                or ["Degree or equivalent practical experience"],
                "selection_criteria": current.selection_criteria
                or ["Evidence of production ML ownership"],
            }
        )
        return AssistantTurnPlan(
            intent=AssistantIntent.DRAFT_REQUIREMENTS,
            response_message="I drafted the requirements. Add a location and application email.",
            requirements=requirements,
        )

    async def generate_job_description(
        self,
        request: JobDescriptionGenerationRequest,
    ) -> JobDescriptionGenerationResult:
        self.call_count += 1
        if self.errors:
            raise self.errors.pop(0)
        return JobDescriptionGenerationResult(
            description=GeneratedJobDescription(
                title=request.title,
                summary=f"Join our team as a {request.title} in {request.location}.",
                responsibilities=["Build and maintain reliable recruitment platform features."],
                required_skills=request.required_skills,
                preferred_skills=request.preferred_skills,
                qualifications=request.qualifications,
                minimum_experience=request.minimum_experience,
                application_information=(f"Send your application to {request.application_email}."),
            ),
            provider="fake",
            model="fake-jd-model",
            metadata=LLMGenerationMetadata(
                finish_reason="STOP",
                input_tokens=100,
                output_tokens=200,
                total_tokens=300,
            ),
        )

    async def enhance_job_description(
        self,
        request: JobDescriptionEnhancementRequest,
    ) -> JobDescriptionGenerationResult:
        self.enhancement_call_count += 1
        return JobDescriptionGenerationResult(
            description=GeneratedJobDescription(
                title=request.requirements.title,
                summary=(
                    f"Join our team as a {request.requirements.title}. "
                    "This enhanced description applies the relevant company policy."
                ),
                responsibilities=[
                    "Build reliable recruitment features and follow documented policy."
                ],
                required_skills=request.requirements.required_skills,
                preferred_skills=request.requirements.preferred_skills,
                qualifications=request.requirements.qualifications,
                minimum_experience=request.requirements.minimum_experience,
                application_information=(
                    f"Send your application to {request.requirements.application_email}."
                ),
            ),
            provider="fake",
            model="fake-jd-model",
            metadata=LLMGenerationMetadata(finish_reason="STOP"),
        )

    async def evaluate_policy_alignment(
        self,
        request: PolicyAlignmentRequest,
    ) -> PolicyAlignmentResult:
        self.policy_call_count += 1
        if self.policy_errors:
            raise self.policy_errors.pop(0)
        return PolicyAlignmentResult(
            evaluation=PolicyAlignmentEvaluation(
                findings=[
                    GeneratedPolicyFinding(
                        evidence_id=evidence.evidence_id,
                        related_jd_section="GENERAL",
                        status="MET",
                        explanation="The generated description reflects this evidence.",
                    )
                    for evidence in request.evidence
                ]
            ),
            provider="fake",
            model="fake-jd-model",
            metadata=LLMGenerationMetadata(finish_reason="STOP"),
        )

    async def extract_candidate_profile(
        self,
        request: CandidateProfileExtractionRequest,
    ) -> CandidateProfileExtractionResult:
        self.profile_call_count += 1
        if self.profile_errors:
            raise self.profile_errors.pop(0)
        evidence_id = request.evidence[0].evidence_id
        profile = self.profile_override or CandidateProfileData(
            contact_details=[
                CandidateContactDetail(
                    kind=ContactDetailKind.EMAIL,
                    value="synthetic@example.test",
                    evidence_chunk_ids=[evidence_id],
                )
            ],
            education=[
                CandidateEducation(
                    qualification="BSc Computer Science",
                    institution="Synthetic University",
                    evidence_chunk_ids=[evidence_id],
                )
            ],
            work_history=[
                CandidateWorkHistory(
                    role="Software Engineer",
                    employer="Example Systems",
                    description="Built Python services.",
                    evidence_chunk_ids=[evidence_id],
                )
            ],
            skills=[CandidateSkill(name="Python", evidence_chunk_ids=[evidence_id])],
            certifications=[
                CandidateCertification(
                    name="Synthetic Cloud Certification",
                    issuer="Example Institute",
                    evidence_chunk_ids=[evidence_id],
                )
            ],
            projects=[
                CandidateProject(
                    name="Synthetic platform",
                    technologies=["Python"],
                    evidence_chunk_ids=[evidence_id],
                )
            ],
        )
        return CandidateProfileExtractionResult(
            profile=profile,
            provider="fake",
            model="fake-profile-model",
        )

    async def evaluate_candidate_screening(
        self,
        request: CandidateScreeningEvaluationRequest,
    ) -> CandidateScreeningEvaluationResult:
        self.screening_call_count += 1
        if self.screening_errors:
            raise self.screening_errors.pop(0)
        evaluation = self.screening_override or CandidateScreeningEvaluation(
            matches=[
                GeneratedRequirementMatch(
                    requirement_id=requirement.requirement_id,
                    status=(
                        RequirementMatchStatus.MET
                        if requirement.evidence_chunk_ids
                        else RequirementMatchStatus.UNMET
                    ),
                    justification=(
                        "The processed CV evidence demonstrates this requirement."
                        if requirement.evidence_chunk_ids
                        else "Sufficient supporting CV evidence was not found."
                    ),
                    evidence_chunk_ids=requirement.evidence_chunk_ids[:1],
                )
                for requirement in request.requirements
            ]
        )
        return CandidateScreeningEvaluationResult(
            evaluation=evaluation,
            provider="fake",
            model="fake-screening-model",
        )


class FakeEmbeddingAdapter:
    model_name = "nomic-embed-text"
    dimension = 768

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 767 for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0] + [0.0] * 767


class FakePublisherAdapter:
    provider = "BLUESKY"

    def __init__(self) -> None:
        self.contents: list[JobPublicationContent] = []
        self.errors: list[PublisherError] = []

    async def publish_job(self, content: JobPublicationContent) -> PublicationResult:
        self.contents.append(content)
        if self.errors:
            raise self.errors.pop(0)
        return PublicationResult(
            provider=self.provider,
            external_post_uri=("at://did:plc:recrunion/app.bsky.feed.post/3m3recruniontest"),
            external_record_id="bafyreirecruniontest",
            external_url=("https://bsky.app/profile/did:plc:recrunion/post/3m3recruniontest"),
            published_at=content.created_at,
        )


@pytest.fixture
def database_engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(database_engine: Engine) -> Iterator[Session]:
    with Session(database_engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def fake_llm_adapter() -> FakeLLMAdapter:
    return FakeLLMAdapter()


@pytest.fixture
def fake_embedding_adapter() -> FakeEmbeddingAdapter:
    return FakeEmbeddingAdapter()


@pytest.fixture
def fake_publisher_adapter() -> FakePublisherAdapter:
    return FakePublisherAdapter()


@pytest.fixture
def application(
    database_engine: Engine,
    tmp_path,
    fake_llm_adapter: FakeLLMAdapter,
    fake_embedding_adapter: FakeEmbeddingAdapter,
    fake_publisher_adapter: FakePublisherAdapter,
) -> Iterator[FastAPI]:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with Session(database_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_llm_adapter] = lambda: fake_llm_adapter
    app.dependency_overrides[get_embedding_adapter] = lambda: fake_embedding_adapter
    app.dependency_overrides[get_publisher_adapter] = lambda: fake_publisher_adapter
    app.dependency_overrides[get_application_document_storage] = lambda: LocalDocumentStorage(
        tmp_path / "applications",
        10 * 1024 * 1024,
    )
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def job_payload() -> dict[str, Any]:
    return {
        "title": "Backend Engineer",
        "location": "Dhaka",
        "employment_type": "FULL_TIME",
        "application_email": "jobs@example.com",
        "required_skills": ["Python", "PostgreSQL"],
        "preferred_skills": ["FastAPI"],
        "minimum_experience": 3,
        "qualifications": ["Bachelor's degree or equivalent experience"],
        "selection_criteria": ["Evidence of production API ownership"],
    }
