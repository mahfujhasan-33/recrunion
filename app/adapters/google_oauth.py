from dataclasses import dataclass
from typing import Any

import httpx


class GoogleOAuthError(Exception):
    """Internal OAuth failure translated by the owning provider adapter."""


@dataclass(frozen=True)
class GoogleOAuthCredentials:
    client_id: str
    client_secret: str
    refresh_token: str

    @property
    def configured(self) -> bool:
        return all(
            value.strip() for value in (self.client_id, self.client_secret, self.refresh_token)
        )


class GoogleOAuthClient:
    """Exchange an operator-provided refresh token for a short-lived access token."""

    def __init__(
        self,
        credentials: GoogleOAuthCredentials,
        *,
        token_url: str = "https://oauth2.googleapis.com/token",
    ) -> None:
        self._credentials = credentials
        self._token_url = token_url

    @property
    def configured(self) -> bool:
        return self._credentials.configured

    async def access_token(self, client: httpx.AsyncClient) -> str:
        if not self.configured:
            raise GoogleOAuthError("Google OAuth credentials are not configured.")
        response = await client.post(
            self._token_url,
            data={
                "client_id": self._credentials.client_id,
                "client_secret": self._credentials.client_secret,
                "refresh_token": self._credentials.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if not response.is_success:
            raise GoogleOAuthError("Google OAuth rejected the configured credentials.")
        payload = _json_object(response)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise GoogleOAuthError("Google OAuth returned an invalid token response.")
        return token


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as error:
        raise GoogleOAuthError("Google returned an unreadable response.") from error
    if not isinstance(payload, dict):
        raise GoogleOAuthError("Google returned an invalid response.")
    return payload
