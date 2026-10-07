from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, Field


class JobPublicationContent(BaseModel):
    """Provider-neutral content for one approved-job announcement."""

    text: str = Field(min_length=1, max_length=300)
    created_at: datetime


class PublicationResult(BaseModel):
    """Normalized successful result returned by a publishing provider."""

    provider: str
    external_post_uri: str
    external_record_id: str | None = None
    external_url: str | None = None
    published_at: datetime


class PublisherAdapter(Protocol):
    async def publish_job(
        self,
        content: JobPublicationContent,
    ) -> PublicationResult:
        """Publish an approved-job announcement and return normalized metadata."""
