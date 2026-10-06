from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.database import Base, get_db_session
from app.dependencies import get_llm_adapter
from app.errors import LLMProviderError
from app.main import create_app
from app.schemas.job_descriptions import (
    GeneratedJobDescription,
    JobDescriptionGenerationRequest,
    JobDescriptionGenerationResult,
    LLMGenerationMetadata,
)


class FakeLLMAdapter:
    """Deterministic adapter used by graph, service, and API tests."""

    def __init__(self) -> None:
        self.call_count = 0
        self.errors: list[LLMProviderError] = []

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
def application(
    database_engine: Engine,
    fake_llm_adapter: FakeLLMAdapter,
) -> Iterator[FastAPI]:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with Session(database_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_llm_adapter] = lambda: fake_llm_adapter
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
