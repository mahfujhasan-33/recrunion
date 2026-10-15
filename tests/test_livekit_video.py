from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from livekit import api

from app.adapters.livekit_video import LiveKitVideoAdapter
from app.adapters.video import (
    VideoParticipantRole,
    video_participant_identity,
    video_room_name,
)
from app.errors import VideoConfigurationError, VideoProviderError

API_KEY = "synthetic-api-key"
API_SECRET = "synthetic-livekit-secret-that-is-long-enough"


class FakeRoomService:
    def __init__(self) -> None:
        self.identities = ["candidate_example", "agent_example"]
        self.created_rooms: list[str] = []
        self.listed_room: str | None = None
        self.deleted_room: str | None = None
        self.error: Exception | None = None

    async def create_room(self, request):
        if self.error is not None:
            raise self.error
        self.created_rooms.append(request.name)
        return SimpleNamespace(name=request.name)

    async def list_participants(self, request):
        if self.error is not None:
            raise self.error
        self.listed_room = request.room
        return SimpleNamespace(
            participants=[SimpleNamespace(identity=identity) for identity in self.identities]
        )

    async def delete_room(self, request):
        if self.error is not None:
            raise self.error
        self.deleted_room = request.room
        return SimpleNamespace()


class FakeAgentDispatchService:
    def __init__(self) -> None:
        self.dispatches = []
        self.create_requests = []
        self.error: Exception | None = None

    async def list_dispatch(self, _room_name: str):
        if self.error is not None:
            raise self.error
        return self.dispatches

    async def create_dispatch(self, request):
        if self.error is not None:
            raise self.error
        self.create_requests.append(request)
        dispatch = SimpleNamespace(
            agent_name=request.agent_name,
            room=request.room,
            metadata=request.metadata,
        )
        self.dispatches.append(dispatch)
        return dispatch


class FakeLiveKitClient:
    def __init__(self) -> None:
        self.room = FakeRoomService()
        self.agent_dispatch = FakeAgentDispatchService()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def build_adapter(client: FakeLiveKitClient | None = None) -> LiveKitVideoAdapter:
    return LiveKitVideoAdapter(
        url="wss://synthetic.livekit.example.test",
        api_key=API_KEY,
        api_secret=API_SECRET,
        token_ttl_seconds=600,
        client_factory=(lambda: client) if client is not None else None,
    )


@pytest.mark.parametrize(
    "role",
    [VideoParticipantRole.CANDIDATE, VideoParticipantRole.INTERVIEWER],
)
def test_participant_token_is_opaque_short_lived_and_room_scoped(
    role: VideoParticipantRole,
) -> None:
    interview_id = uuid4()
    credentials = build_adapter().create_participant_credentials(interview_id, role)
    claims = jwt.decode(credentials.participant_token, API_SECRET, algorithms=["HS256"])
    grants = claims["video"]

    assert credentials.provider_url == "wss://synthetic.livekit.example.test"
    assert credentials.room_name == video_room_name(interview_id)
    assert credentials.participant_identity == video_participant_identity(interview_id, role)
    assert claims["sub"] == credentials.participant_identity
    assert claims["exp"] - claims["nbf"] == 600
    assert grants == {
        "roomCreate": False,
        "roomList": False,
        "roomRecord": False,
        "roomAdmin": False,
        "roomJoin": True,
        "room": video_room_name(interview_id),
        "canPublish": True,
        "canSubscribe": True,
        "canPublishData": False,
        "canPublishSources": ["camera", "microphone"],
        "canUpdateOwnMetadata": False,
        "ingressAdmin": False,
    }
    assert "candidate@example" not in credentials.room_name
    assert API_SECRET not in credentials.participant_token


def test_missing_or_invalid_configuration_creates_no_token() -> None:
    for url, key, secret in [
        ("", API_KEY, API_SECRET),
        ("wss://synthetic.livekit.example.test", "", API_SECRET),
        ("wss://synthetic.livekit.example.test", API_KEY, ""),
        ("https://not-a-livekit-websocket.example.test", API_KEY, API_SECRET),
    ]:
        adapter = LiveKitVideoAdapter(
            url=url,
            api_key=key,
            api_secret=secret,
            token_ttl_seconds=600,
        )
        assert adapter.configured is False
        with pytest.raises(VideoConfigurationError):
            adapter.create_participant_credentials(uuid4(), VideoParticipantRole.CANDIDATE)


@pytest.mark.asyncio
async def test_participant_listing_and_room_termination_are_normalized() -> None:
    interview_id = uuid4()
    listing_client = FakeLiveKitClient()
    listing_adapter = build_adapter(listing_client)

    participants = await listing_adapter.list_participants(interview_id)

    assert [participant.identity for participant in participants] == [
        "candidate_example",
        "agent_example",
    ]
    assert listing_client.room.listed_room == video_room_name(interview_id)
    assert listing_client.closed is True

    deletion_client = FakeLiveKitClient()
    await build_adapter(deletion_client).terminate_room(interview_id)
    assert deletion_client.room.deleted_room == video_room_name(interview_id)
    assert deletion_client.closed is True


@pytest.mark.asyncio
async def test_provider_failure_is_translated_and_client_is_closed() -> None:
    client = FakeLiveKitClient()
    client.room.error = RuntimeError("raw provider detail")

    with pytest.raises(VideoProviderError, match="temporarily unavailable"):
        await build_adapter(client).list_participants(uuid4())

    assert client.closed is True


@pytest.mark.asyncio
async def test_transcription_dispatch_is_idempotent_and_contains_only_supplied_metadata() -> None:
    interview_id = uuid4()
    client = FakeLiveKitClient()
    adapter = build_adapter(client)
    metadata = '{"callback_url":"https://example.test/callback","interview_id":"opaque"}'

    await adapter.ensure_transcription_dispatch(
        interview_id,
        agent_name="recrunion-transcriber",
        metadata=metadata,
    )
    await adapter.ensure_transcription_dispatch(
        interview_id,
        agent_name="recrunion-transcriber",
        metadata=metadata,
    )

    assert len(client.agent_dispatch.create_requests) == 1
    request = client.agent_dispatch.create_requests[0]
    assert request.room == video_room_name(interview_id)
    assert request.agent_name == "recrunion-transcriber"
    assert request.metadata == metadata
    assert "secret" not in request.metadata.casefold()


@pytest.mark.asyncio
async def test_transcription_dispatch_creates_missing_room_before_dispatch() -> None:
    interview_id = uuid4()
    client = FakeLiveKitClient()
    adapter = build_adapter(client)

    async def list_dispatch_after_room_creation(room_name: str):
        if not client.room.created_rooms:
            raise api.ServerError(
                "not_found",
                "requested room does not exist",
                status=404,
            )
        return []

    client.agent_dispatch.list_dispatch = list_dispatch_after_room_creation

    await adapter.ensure_transcription_dispatch(
        interview_id,
        agent_name="recrunion-transcriber",
        metadata='{"interview_id":"opaque"}',
    )

    assert client.room.created_rooms == [video_room_name(interview_id)]
    assert len(client.agent_dispatch.create_requests) == 1
    assert client.closed is True
