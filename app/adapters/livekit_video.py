from collections.abc import Callable
from datetime import timedelta
from typing import Protocol
from uuid import UUID

from livekit import api

from app.adapters.video import (
    VideoAdapter,
    VideoParticipant,
    VideoParticipantCredentials,
    VideoParticipantRole,
    video_participant_identity,
    video_room_name,
)
from app.errors import VideoConfigurationError, VideoProviderError


class LiveKitRoomService(Protocol):
    async def list_participants(
        self,
        request: api.ListParticipantsRequest,
    ) -> api.ListParticipantsResponse: ...

    async def delete_room(
        self,
        request: api.DeleteRoomRequest,
    ) -> api.DeleteRoomResponse: ...


class LiveKitAPIClient(Protocol):
    room: LiveKitRoomService

    async def aclose(self) -> None: ...


class LiveKitVideoAdapter(VideoAdapter):
    """Provide scoped LiveKit credentials and backend room operations."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        api_secret: str,
        token_ttl_seconds: int,
        client_factory: Callable[[], LiveKitAPIClient] | None = None,
    ) -> None:
        self._url = url.strip()
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._token_ttl_seconds = token_ttl_seconds
        self._client_factory = client_factory or self._build_client

    @property
    def configured(self) -> bool:
        return bool(
            self._url
            and self._api_key
            and self._api_secret
            and self._url.casefold().startswith(("ws://", "wss://"))
        )

    def create_participant_credentials(
        self,
        interview_id: UUID,
        role: VideoParticipantRole,
    ) -> VideoParticipantCredentials:
        self._ensure_configured()
        room_name = video_room_name(interview_id)
        identity = video_participant_identity(interview_id, role)
        token = (
            api.AccessToken(self._api_key, self._api_secret)
            .with_identity(identity)
            .with_ttl(timedelta(seconds=self._token_ttl_seconds))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=False,
                    can_publish_sources=["camera", "microphone"],
                    room_admin=False,
                    room_create=False,
                    room_list=False,
                    room_record=False,
                    ingress_admin=False,
                    can_update_own_metadata=False,
                )
            )
            .to_jwt()
        )
        return VideoParticipantCredentials(
            provider_url=self._url,
            participant_token=token,
            room_name=room_name,
            participant_identity=identity,
        )

    async def list_participants(self, interview_id: UUID) -> list[VideoParticipant]:
        self._ensure_configured()
        client = self._client_factory()
        try:
            response = await client.room.list_participants(
                api.ListParticipantsRequest(room=video_room_name(interview_id))
            )
            return [VideoParticipant(identity=item.identity) for item in response.participants]
        except VideoProviderError:
            raise
        except Exception as error:
            raise VideoProviderError(
                "The live interview provider is temporarily unavailable."
            ) from error
        finally:
            await client.aclose()

    async def terminate_room(self, interview_id: UUID) -> None:
        self._ensure_configured()
        client = self._client_factory()
        try:
            await client.room.delete_room(api.DeleteRoomRequest(room=video_room_name(interview_id)))
        except VideoProviderError:
            raise
        except Exception as error:
            raise VideoProviderError(
                "The live interview provider room could not be closed."
            ) from error
        finally:
            await client.aclose()

    def _ensure_configured(self) -> None:
        if not self.configured:
            raise VideoConfigurationError(
                "Live interviews are not configured. Add the LiveKit server settings and restart."
            )

    def _build_client(self) -> LiveKitAPIClient:
        return api.LiveKitAPI(
            self._url,
            api_key=self._api_key,
            api_secret=self._api_secret,
        )
