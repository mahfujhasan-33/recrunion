import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.adapters.google_oauth import GoogleOAuthClient, GoogleOAuthError
from app.errors import (
    GmailAuthenticationError,
    GmailConfigurationError,
    GmailSearchError,
)


@dataclass(frozen=True)
class GmailAttachment:
    attachment_id: str
    filename: str
    mime_type: str
    size: int
    content: bytes | None
    fetch_error: str | None = None


@dataclass(frozen=True)
class GmailApplicationMessage:
    message_id: str
    received_at: datetime
    attachments: tuple[GmailAttachment, ...]


class GmailApplicationSource:
    """Read application messages without exposing mailbox transport to services."""

    def __init__(
        self,
        *,
        oauth: GoogleOAuthClient,
        api_base_url: str = "https://gmail.googleapis.com/gmail/v1",
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._oauth = oauth
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def fetch_job_applications(self, application_code: str) -> list[GmailApplicationMessage]:
        if not self._oauth.configured:
            raise GmailConfigurationError("Gmail application intake is not configured.")
        exact_subject = f"[{application_code}] Application"
        query = f'subject:"{exact_subject}" has:attachment'
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                token = await self._oauth.access_token(client)
                headers = {"Authorization": f"Bearer {token}"}
                message_ids = await self._list_message_ids(client, headers, query)
                messages: list[GmailApplicationMessage] = []
                for message_id in message_ids:
                    message = await self._get_message(client, headers, message_id)
                    if self._subject(message) != exact_subject:
                        continue
                    messages.append(await self._normalize_message(client, headers, message))
                return messages
        except GoogleOAuthError as error:
            raise GmailAuthenticationError(
                "Gmail rejected the configured OAuth credentials."
            ) from error
        except httpx.TimeoutException as error:
            raise GmailSearchError("Gmail did not respond before the timeout.") from error
        except httpx.RequestError as error:
            raise GmailSearchError("Gmail could not be reached.") from error

    async def _list_message_ids(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        query: str,
    ) -> list[str]:
        message_ids: list[str] = []
        page_token: str | None = None
        while True:
            params: dict[str, str | int] = {"q": query, "maxResults": 100}
            if page_token:
                params["pageToken"] = page_token
            response = await client.get(
                f"{self._api_base_url}/users/me/messages",
                headers=headers,
                params=params,
            )
            self._raise_search_error(response)
            payload = self._json_object(response)
            for item in payload.get("messages", []):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    message_ids.append(item["id"])
            token = payload.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return message_ids
            page_token = token

    async def _get_message(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        message_id: str,
    ) -> dict[str, Any]:
        response = await client.get(
            f"{self._api_base_url}/users/me/messages/{message_id}",
            headers=headers,
            params={"format": "full"},
        )
        self._raise_search_error(response)
        return self._json_object(response)

    async def _normalize_message(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        message: dict[str, Any],
    ) -> GmailApplicationMessage:
        message_id = str(message.get("id", ""))
        if not message_id:
            raise GmailSearchError("Gmail returned a message without an identifier.")
        attachments: list[GmailAttachment] = []
        for part in self._attachment_parts(message.get("payload")):
            body = part.get("body") if isinstance(part.get("body"), dict) else {}
            attachment_id = str(body.get("attachmentId") or f"inline:{len(attachments)}")
            try:
                data = body.get("data")
                if not isinstance(data, str) or not data:
                    provider_id = body.get("attachmentId")
                    if not isinstance(provider_id, str) or not provider_id:
                        raise ValueError
                    response = await client.get(
                        f"{self._api_base_url}/users/me/messages/{message_id}/attachments/"
                        f"{provider_id}",
                        headers=headers,
                    )
                    if response.status_code in {401, 403}:
                        raise GmailAuthenticationError("Gmail rejected the attachment request.")
                    if not response.is_success:
                        raise ValueError
                    data = self._json_object(response).get("data")
                content = self._decode_data(data)
                error = None
            except GmailAuthenticationError:
                raise
            except (ValueError, TypeError):
                content = None
                error = "GMAIL_ATTACHMENT_FAILED"
            attachments.append(
                GmailAttachment(
                    attachment_id=attachment_id,
                    filename=str(part.get("filename") or "attachment"),
                    mime_type=str(part.get("mimeType") or "application/octet-stream"),
                    size=int(body.get("size") or (len(content) if content else 0)),
                    content=content,
                    fetch_error=error,
                )
            )
        raw_received = message.get("internalDate")
        try:
            received_at = datetime.fromtimestamp(int(str(raw_received)) / 1000, tz=UTC)
        except (TypeError, ValueError, OSError):
            received_at = datetime.now(UTC)
        return GmailApplicationMessage(message_id, received_at, tuple(attachments))

    @classmethod
    def _attachment_parts(cls, payload: object) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        found: list[dict[str, Any]] = []
        filename = payload.get("filename")
        body = payload.get("body")
        if isinstance(filename, str) and filename and isinstance(body, dict):
            found.append(payload)
        for child in payload.get("parts", []):
            found.extend(cls._attachment_parts(child))
        return found

    @staticmethod
    def _subject(message: dict[str, Any]) -> str:
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return ""
        for header in payload.get("headers", []):
            if isinstance(header, dict) and str(header.get("name", "")).casefold() == "subject":
                return str(header.get("value", "")).strip()
        return ""

    @staticmethod
    def _decode_data(data: object) -> bytes:
        if not isinstance(data, str) or not data:
            raise ValueError
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding)

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise GmailSearchError("Gmail returned an unreadable response.") from error
        if not isinstance(payload, dict):
            raise GmailSearchError("Gmail returned an invalid response.")
        return payload

    @staticmethod
    def _raise_search_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        if response.status_code in {401, 403}:
            raise GmailAuthenticationError("Gmail rejected the mailbox request.")
        raise GmailSearchError("Gmail could not search the application mailbox.")
