from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.job_publications import PublicationStatus


class PublicationProvider(StrEnum):
    BLOGGER = "BLOGGER"
    BLUESKY = "BLUESKY"


class JobPublicationResponse(BaseModel):
    id: UUID
    job_id: UUID
    processing_task_id: UUID
    provider: str
    status: PublicationStatus
    attempt_number: int
    published_jd_version: int
    external_post_uri: str | None
    external_record_id: str | None
    external_url: str | None
    error_code: str | None
    error_message_safe: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicationConfirmationRequest(BaseModel):
    confirmed: bool
    providers: list[PublicationProvider] | None = None


class JobPublicationRequest(BaseModel):
    providers: list[PublicationProvider] = Field(
        default_factory=lambda: [PublicationProvider.BLUESKY], min_length=1, max_length=2
    )

    @field_validator("providers")
    @classmethod
    def providers_are_unique(
        cls, providers: list[PublicationProvider]
    ) -> list[PublicationProvider]:
        if len(providers) != len(set(providers)):
            raise ValueError("Publication providers must be unique.")
        return providers


class JobPublicationRetryRequest(BaseModel):
    provider: PublicationProvider


class JobPublicationBatchResponse(BaseModel):
    processing_task_id: UUID
    publications: list[JobPublicationResponse]
    id: UUID | None = None
    status: PublicationStatus | None = None
    attempt_number: int | None = None

    def model_post_init(self, __context: object) -> None:
        if not self.publications:
            return
        first = self.publications[0]
        self.id = first.id
        self.status = first.status
        self.attempt_number = first.attempt_number
