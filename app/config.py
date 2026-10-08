from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "postgresql+psycopg://recrunion:CHANGE_ME@localhost:5432/recrunion"
    log_level: str = "INFO"
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_timeout_seconds: float = Field(default=30.0, gt=0)
    gemini_max_retries: int = Field(default=1, ge=0, le=5)
    company_documents_root: Path = Path("data/company_documents")
    max_company_document_size_mb: int = Field(default=10, ge=1, le=100)
    applications_root: Path = Path("data/applications")
    max_cv_size_mb: int = Field(default=10, ge=1, le=100)
    embedding_provider: Literal["ollama"] = "ollama"
    embedding_model: str = "nomic-embed-text"
    embedding_dimension: int = Field(default=768, ge=768, le=768)
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_timeout_seconds: float = Field(default=60.0, gt=0)
    policy_retrieval_candidate_count: int = Field(default=20, ge=1, le=100)
    policy_retrieval_top_k: int = Field(default=8, ge=1, le=30)
    policy_retrieval_min_similarity: float = Field(default=0.3, ge=-1, le=1)
    publisher_provider: Literal["bluesky"] = "bluesky"
    bluesky_identifier: str = ""
    bluesky_app_password: str = ""
    bluesky_service_url: str = "https://bsky.social"
    bluesky_timeout_seconds: float = Field(default=15.0, gt=0)
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the cached process configuration."""

    return Settings()
