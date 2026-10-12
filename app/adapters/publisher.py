from datetime import datetime
from typing import Protocol

from pydantic import AliasChoices, BaseModel, Field


class JobPublicationContent(BaseModel):
    """Provider-neutral content for one approved-job announcement."""

    title: str = Field(default="Job opportunity", min_length=1, max_length=200)
    short_text: str = Field(
        min_length=1,
        max_length=300,
        validation_alias=AliasChoices("short_text", "text"),
    )
    full_html: str = Field(default="<p>Job opportunity</p>", min_length=1)
    jd_version: int = Field(default=1, ge=1)
    created_at: datetime

    @property
    def text(self) -> str:
        """Return the concise channel text used by the M3 contract."""

        return self.short_text


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
