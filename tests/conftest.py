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
from app.main import create_app


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
def application(database_engine: Engine) -> Iterator[FastAPI]:
    app = create_app()

    def override_db_session() -> Iterator[Session]:
        with Session(database_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
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
