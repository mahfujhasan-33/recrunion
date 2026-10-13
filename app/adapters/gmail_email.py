import base64
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import httpx

from app.adapters.email import EmailAdapter, EmailSendResult, OutboundEmail
from app.adapters.google_oauth import GoogleOAuthClient, GoogleOAuthError
from app.errors import (
    GmailAuthenticationError,
    GmailConfigurationError,
    GmailSendError,
)


class GmailEmailAdapter(EmailAdapter):
    """Send recruiter-approved outbound messages through Gmail's REST API."""

    def __init__(
        self,
        *,
        oauth: GoogleOAuthClient,
        sender: str,
        api_base_url: str = "https://gmail.googleapis.com/gmail/v1",
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._oauth = oauth
        self._sender = sender.strip()
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def send(self, message: OutboundEmail) -> EmailSendResult:
        if not self._oauth.configured or not self._sender:
            raise GmailConfigurationError("Outbound Gmail invitations are not configured.")
        raw_message = self._encode_message(message)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                token = await self._oauth.access_token(client)
                response = await client.post(
                    f"{self._api_base_url}/users/me/messages/send",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"raw": raw_message},
                )
        except GoogleOAuthError as error:
            raise GmailAuthenticationError(
                "Gmail rejected the outbound OAuth credentials."
            ) from error
        except httpx.TimeoutException as error:
            raise GmailSendError("Gmail did not respond before the timeout.") from error
        except httpx.RequestError as error:
            raise GmailSendError("Gmail could not be reached.") from error
        if response.status_code in {401, 403}:
            raise GmailAuthenticationError(
                "Gmail rejected the send request. Confirm the gmail.send scope."
            )
        if not response.is_success:
            raise GmailSendError("Gmail could not send the interview invitation.")
        payload = self._json_object(response)
        message_id = payload.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise GmailSendError("Gmail returned an invalid send response.")
        return EmailSendResult(provider_message_id=message_id, sent_at=datetime.now(UTC))

    def _encode_message(self, message: OutboundEmail) -> str:
        mime = EmailMessage()
        mime["To"] = message.recipient
        mime["From"] = self._sender
        mime["Subject"] = message.subject
        mime.set_content(message.text_body)
        return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii").rstrip("=")

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise GmailSendError("Gmail returned an unreadable send response.") from error
        if not isinstance(payload, dict):
            raise GmailSendError("Gmail returned an invalid send response.")
        return payload
