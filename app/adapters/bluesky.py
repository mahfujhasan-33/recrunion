from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from app.adapters.publisher import JobPublicationContent, PublicationResult
from app.errors import (
    PublisherAuthenticationError,
    PublisherConfigurationError,
    PublisherInvalidResponseError,
    PublisherRateLimitError,
    PublisherRejectedContentError,
    PublisherTimeoutError,
    PublisherUnavailableError,
)


class BlueskyPublisher:
    """Publish Bluesky feed records through the official AT Protocol XRPC API."""

    provider = "BLUESKY"

    def __init__(
        self,
        *,
        identifier: str,
        app_password: str,
        service_url: str = "https://bsky.social",
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._identifier = identifier.strip()
        self._app_password = app_password
        self._service_url = service_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def publish_job(self, content: JobPublicationContent) -> PublicationResult:
        if not self._identifier or not self._app_password:
            raise PublisherConfigurationError("Bluesky publishing credentials are not configured.")

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                session = await client.post(
                    f"{self._service_url}/xrpc/com.atproto.server.createSession",
                    json={
                        "identifier": self._identifier,
                        "password": self._app_password,
                    },
                )
                self._raise_for_status(session, authenticating=True)
                session_data = self._json_object(session)
                access_token = session_data.get("accessJwt")
                repository_did = session_data.get("did")
                if not isinstance(access_token, str) or not isinstance(repository_did, str):
                    raise PublisherInvalidResponseError(
                        "Bluesky returned an invalid authentication response."
                    )

                response = await client.post(
                    f"{self._service_url}/xrpc/com.atproto.repo.createRecord",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "repo": repository_did,
                        "collection": "app.bsky.feed.post",
                        "record": {
                            "$type": "app.bsky.feed.post",
                            "text": content.text,
                            "createdAt": content.created_at.isoformat().replace("+00:00", "Z"),
                        },
                    },
                )
                self._raise_for_status(response, authenticating=False)
                result = self._json_object(response)
        except httpx.TimeoutException as error:
            raise PublisherTimeoutError("Bluesky did not respond before the timeout.") from error
        except httpx.RequestError as error:
            raise PublisherUnavailableError("Bluesky could not be reached.") from error

        uri = result.get("uri")
        cid = result.get("cid")
        if not isinstance(uri, str) or not uri.startswith("at://"):
            raise PublisherInvalidResponseError("Bluesky returned an invalid publication result.")
        if cid is not None and not isinstance(cid, str):
            raise PublisherInvalidResponseError("Bluesky returned an invalid publication result.")

        try:
            return PublicationResult(
                provider=self.provider,
                external_post_uri=uri,
                external_record_id=cid,
                external_url=self._external_url(uri),
                published_at=content.created_at,
            )
        except ValidationError as error:
            raise PublisherInvalidResponseError(
                "Bluesky returned an invalid publication result."
            ) from error

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise PublisherInvalidResponseError(
                "Bluesky returned an unreadable response."
            ) from error
        if not isinstance(body, dict):
            raise PublisherInvalidResponseError("Bluesky returned an invalid response.")
        return body

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, authenticating: bool) -> None:
        if response.is_success:
            return
        if response.status_code in {401, 403} or (authenticating and response.status_code == 400):
            raise PublisherAuthenticationError(
                "Bluesky rejected the configured publishing credentials."
            )
        if response.status_code == 429:
            raise PublisherRateLimitError(
                "Bluesky is rate limiting publication requests. Try again later."
            )
        if response.status_code == 400:
            raise PublisherRejectedContentError("Bluesky rejected the publication content.")
        raise PublisherUnavailableError("Bluesky could not complete the publication request.")

    @staticmethod
    def _external_url(uri: str) -> str | None:
        parts = uri.removeprefix("at://").split("/")
        if len(parts) != 3 or parts[1] != "app.bsky.feed.post":
            return None
        repository, _, record_key = parts
        return (
            f"https://bsky.app/profile/{quote(repository, safe=':')}/post/"
            f"{quote(record_key, safe='')}"
        )
