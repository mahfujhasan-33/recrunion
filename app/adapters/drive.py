import json
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.adapters.google_oauth import GoogleOAuthClient, GoogleOAuthError
from app.errors import (
    DriveArchiveError,
    DriveAuthenticationError,
    DriveConfigurationError,
    DriveFolderError,
)

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


@dataclass(frozen=True)
class DriveFolder:
    id: str
    url: str | None


@dataclass(frozen=True)
class DriveArchivedDocument:
    id: str
    url: str | None


class DriveArchiveAdapter(Protocol):
    async def create_folder(self, name: str) -> DriveFolder: ...

    async def archive_pdf(
        self, folder_id: str, filename: str, content: bytes
    ) -> DriveArchivedDocument: ...


class GoogleDriveArchiveAdapter:
    """Archive locally managed PDFs through the official Google Drive REST API."""

    def __init__(
        self,
        *,
        oauth: GoogleOAuthClient,
        parent_folder_id: str = "root",
        api_base_url: str = "https://www.googleapis.com/drive/v3",
        upload_base_url: str = "https://www.googleapis.com/upload/drive/v3",
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._oauth = oauth
        self._parent_folder_id = parent_folder_id.strip() or "root"
        self._api_base_url = api_base_url.rstrip("/")
        self._upload_base_url = upload_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def create_folder(self, name: str) -> DriveFolder:
        if not self._oauth.configured:
            raise DriveConfigurationError("Google Drive archive is not configured.")
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                token = await self._oauth.access_token(client)
                response = await client.post(
                    f"{self._api_base_url}/files",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"fields": "id,webViewLink"},
                    json={
                        "name": name,
                        "mimeType": FOLDER_MIME_TYPE,
                        "parents": [self._parent_folder_id],
                    },
                )
        except GoogleOAuthError as error:
            raise DriveAuthenticationError(
                "Google Drive rejected the configured OAuth credentials."
            ) from error
        except (httpx.TimeoutException, httpx.RequestError) as error:
            raise DriveFolderError("Google Drive could not create the job folder.") from error
        payload = self._checked_json(response, DriveFolderError, "create the job folder")
        folder_id = payload.get("id")
        if not isinstance(folder_id, str) or not folder_id:
            raise DriveFolderError("Google Drive returned an invalid folder result.")
        url = payload.get("webViewLink")
        return DriveFolder(folder_id, url if isinstance(url, str) else None)

    async def archive_pdf(
        self, folder_id: str, filename: str, content: bytes
    ) -> DriveArchivedDocument:
        if not self._oauth.configured:
            raise DriveConfigurationError("Google Drive archive is not configured.")
        boundary = "recrunion_multipart_boundary"
        metadata = json.dumps({"name": filename, "parents": [folder_id]}).encode()
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + metadata
            + f"\r\n--{boundary}\r\nContent-Type: application/pdf\r\n\r\n".encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, transport=self._transport
            ) as client:
                token = await self._oauth.access_token(client)
                response = await client.post(
                    f"{self._upload_base_url}/files",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": f"multipart/related; boundary={boundary}",
                    },
                    params={"uploadType": "multipart", "fields": "id,webViewLink"},
                    content=body,
                )
        except GoogleOAuthError as error:
            raise DriveAuthenticationError(
                "Google Drive rejected the configured OAuth credentials."
            ) from error
        except (httpx.TimeoutException, httpx.RequestError) as error:
            raise DriveArchiveError("Google Drive could not archive the CV.") from error
        payload = self._checked_json(response, DriveArchiveError, "archive the CV")
        file_id = payload.get("id")
        if not isinstance(file_id, str) or not file_id:
            raise DriveArchiveError("Google Drive returned an invalid archive result.")
        url = payload.get("webViewLink")
        return DriveArchivedDocument(file_id, url if isinstance(url, str) else None)

    @staticmethod
    def _checked_json(
        response: httpx.Response,
        error_type: type[DriveFolderError] | type[DriveArchiveError],
        operation: str,
    ) -> dict[str, object]:
        if response.status_code in {401, 403}:
            raise DriveAuthenticationError("Google Drive rejected the archive request.")
        if not response.is_success:
            raise error_type(f"Google Drive could not {operation}.")
        try:
            payload = response.json()
        except ValueError as error:
            raise error_type("Google Drive returned an unreadable response.") from error
        if not isinstance(payload, dict):
            raise error_type("Google Drive returned an invalid response.")
        return payload
