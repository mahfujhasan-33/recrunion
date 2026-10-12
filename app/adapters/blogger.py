from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.google_oauth import GoogleOAuthClient, GoogleOAuthError
from app.adapters.publisher import JobPublicationContent, PublicationResult
from app.errors import (
    BloggerAuthenticationError,
    BloggerPublishingError,
    PublisherConfigurationError,
    PublisherInvalidResponseError,
    PublisherRateLimitError,
    PublisherTimeoutError,
)


class BloggerPublisher:
    """Publish a complete approved JD through the official Blogger REST API."""

    provider = "BLOGGER"

    def __init__(
        self,
        *,
        blog_id: str,
        oauth: GoogleOAuthClient,
        api_base_url: str = "https://www.googleapis.com/blogger/v3",
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._blog_id = blog_id.strip()
        self._oauth = oauth
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def publish_job(self, content: JobPublicationContent) -> PublicationResult:
        if not self._blog_id or not self._oauth.configured:
            raise PublisherConfigurationError("Blogger publishing is not configured.")
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                access_token = await self._oauth.access_token(client)
                response = await client.post(
                    f"{self._api_base_url}/blogs/{self._blog_id}/posts",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params={"fields": "id,url,published"},
                    json={"title": content.title, "content": content.full_html},
                )
        except GoogleOAuthError as error:
            raise BloggerAuthenticationError(
                "Blogger rejected the configured OAuth credentials."
            ) from error
        except httpx.TimeoutException as error:
            raise PublisherTimeoutError("Blogger did not respond before the timeout.") from error
        except httpx.RequestError as error:
            raise BloggerPublishingError("Blogger could not be reached.") from error

        if response.status_code in {401, 403}:
            raise BloggerAuthenticationError("Blogger rejected the publishing request.")
        if response.status_code == 429:
            raise PublisherRateLimitError(
                "Blogger is rate limiting publication requests. Try again later."
            )
        if not response.is_success:
            raise BloggerPublishingError("Blogger could not publish the approved job.")
        payload = self._json_object(response)
        post_id = payload.get("id")
        url = payload.get("url")
        if not isinstance(post_id, str) or not post_id or not isinstance(url, str) or not url:
            raise PublisherInvalidResponseError("Blogger returned an invalid publication result.")
        published_at = content.created_at
        published = payload.get("published")
        if isinstance(published, str):
            try:
                published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
            except ValueError:
                published_at = datetime.now(UTC)
        return PublicationResult(
            provider=self.provider,
            external_post_uri=f"blogger://{self._blog_id}/{post_id}",
            external_record_id=post_id,
            external_url=url,
            published_at=published_at,
        )

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise PublisherInvalidResponseError(
                "Blogger returned an unreadable response."
            ) from error
        if not isinstance(payload, dict):
            raise PublisherInvalidResponseError("Blogger returned an invalid response.")
        return payload
