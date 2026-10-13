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
    app_base_url: str = "http://localhost:8000"
    jwt_secret: str = ""
    webrtc_stun_urls: str = "stun:stun.l.google.com:19302"
    turn_url: str = ""
    turn_username: str = ""
    turn_password: str = ""
    webrtc_ice_transport_policy: Literal["all", "relay"] = "all"
    webrtc_signaling_ticket_ttl_seconds: int = Field(default=600, ge=30, le=3600)
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
    minimum_cv_text_characters: int = Field(default=100, ge=20, le=5000)
    embedding_provider: Literal["ollama"] = "ollama"
    embedding_model: str = "nomic-embed-text"
    embedding_dimension: int = Field(default=768, ge=768, le=768)
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_timeout_seconds: float = Field(default=60.0, gt=0)
    policy_retrieval_candidate_count: int = Field(default=20, ge=1, le=100)
    policy_retrieval_top_k: int = Field(default=8, ge=1, le=30)
    policy_retrieval_min_similarity: float = Field(default=0.3, ge=-1, le=1)
    screening_retrieval_top_k: int = Field(default=4, ge=1, le=10)
    screening_retrieval_min_similarity: float = Field(default=0.25, ge=-1, le=1)
    publisher_provider: str = "bluesky,blogger"
    bluesky_identifier: str = ""
    bluesky_app_password: str = ""
    bluesky_service_url: str = "https://bsky.social"
    bluesky_timeout_seconds: float = Field(default=15.0, gt=0)
    blogger_blog_id: str = ""
    blogger_client_id: str = ""
    blogger_client_secret: str = ""
    blogger_refresh_token: str = ""
    blogger_api_base_url: str = "https://www.googleapis.com/blogger/v3"
    blogger_timeout_seconds: float = Field(default=20.0, gt=0)
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    gmail_api_base_url: str = "https://gmail.googleapis.com/gmail/v1"
    gmail_timeout_seconds: float = Field(default=20.0, gt=0)
    gmail_send_client_id: str = ""
    gmail_send_client_secret: str = ""
    gmail_send_refresh_token: str = ""
    gmail_send_sender: str = ""
    gmail_send_api_base_url: str = "https://gmail.googleapis.com/gmail/v1"
    gmail_send_timeout_seconds: float = Field(default=20.0, gt=0)
    drive_client_id: str = ""
    drive_client_secret: str = ""
    drive_refresh_token: str = ""
    drive_parent_folder_id: str = "root"
    drive_api_base_url: str = "https://www.googleapis.com/drive/v3"
    drive_upload_base_url: str = "https://www.googleapis.com/upload/drive/v3"
    drive_timeout_seconds: float = Field(default=30.0, gt=0)
    email_auto_sync_enabled: bool = False
    email_sync_interval_seconds: int = Field(default=300, ge=30, le=86400)
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
