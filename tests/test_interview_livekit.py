import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIWebSocketRoute
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.adapters.video import (
    VideoParticipant,
    VideoParticipantRole,
    video_participant_identity,
    video_room_name,
)
from app.dependencies import get_video_adapter
from app.errors import VideoProviderError
from app.models.interviews import InterviewSession, InterviewStatus, InvitationStatus
from app.repositories.interviews import InterviewRepository
from app.repositories.jobs import JobRepository
from app.services.interview_rooms import InterviewRoomService
from app.services.interview_scheduling import hash_token
from tests.conftest import FakeVideoAdapter, authenticated_client
from tests.test_interview_scheduling import create_scored_application


def create_interview(
    session: Session,
    *,
    invitation_token: str = "synthetic-private-invitation-token",
) -> InterviewSession:
    job, application = create_scored_application(session)
    interview = InterviewSession(
        id=uuid4(),
        job_id=job.id,
        application_id=application.id,
        candidate_id=application.candidate_id,
        status=InterviewStatus.SCHEDULED,
        scheduled_start_at=datetime.now(UTC) + timedelta(days=1),
        duration_minutes=45,
        display_timezone="Asia/Dhaka",
        candidate_access_token_hash=hash_token(invitation_token),
        invitation_email="candidate@example.com",
        invitation_status=InvitationStatus.SENT,
    )
    session.add(interview)
    session.commit()
    return interview


def test_room_pages_render_livekit_config_without_recruiter_data_leak(
    application: FastAPI,
    database_engine: Engine,
) -> None:
    token = "synthetic-private-invitation-token"
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session, invitation_token=token)

    with authenticated_client(application) as client:
        recruiter = client.get(f"/interviews/{interview.id}/room")
        candidate = client.get(f"/interviews/join/{token}")

    assert recruiter.status_code == 200
    assert candidate.status_code == 200
    assert "livekit-client@2.22.3" in candidate.text
    assert "interview_room.js" in candidate.text
    assert 'id="remote-interview-audio"' in candidate.text
    assert '"role": "CANDIDATE"' in candidate.text
    assert '"role": "INTERVIEWER"' in recruiter.text
    assert "synthetic-livekit-participant-token" in candidate.text
    assert "Suitability Score" not in candidate.text
    assert "candidate@example.com" not in candidate.text


def test_missing_provider_configuration_renders_safe_state(
    application: FastAPI,
    database_engine: Engine,
) -> None:
    token = "provider-missing-token"
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session, invitation_token=token)
    application.dependency_overrides[get_video_adapter] = lambda: FakeVideoAdapter(configured=False)

    with authenticated_client(application) as client:
        response = client.get(f"/interviews/join/{token}")

    assert response.status_code == 200
    assert "Live interviews are not configured" in response.text
    assert '"participant_token": null' in response.text
    assert '"join_allowed": false' in response.text
    assert interview.id.hex not in "synthetic-livekit-participant-token"


def test_lifecycle_sync_uses_provider_presence_and_candidate_capability(
    application: FastAPI,
    database_engine: Engine,
    fake_video_adapter: FakeVideoAdapter,
) -> None:
    token = "candidate-lifecycle-token"
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session, invitation_token=token)
    room_name = video_room_name(interview.id)
    fake_video_adapter.participants[room_name] = [
        VideoParticipant(video_participant_identity(interview.id, VideoParticipantRole.CANDIDATE))
    ]

    with authenticated_client(application) as client:
        wrong = client.post("/interviews/join/wrong-token/lifecycle-sync")
        waiting = client.post(f"/interviews/join/{token}/lifecycle-sync")
        fake_video_adapter.participants[room_name].append(
            VideoParticipant(
                video_participant_identity(interview.id, VideoParticipantRole.INTERVIEWER)
            )
        )
        active = client.post(f"/api/v1/interviews/{interview.id}/lifecycle-sync")

    assert wrong.status_code == 404
    assert waiting.json()["status"] == "WAITING"
    assert active.json()["status"] == "ACTIVE"
    assert active.json()["started_at"] is not None


def test_candidate_capability_cannot_select_another_room_or_identity(
    application: FastAPI,
    database_engine: Engine,
    fake_video_adapter: FakeVideoAdapter,
) -> None:
    first_token = "first-candidate-capability"
    second_token = "second-candidate-capability"
    with Session(database_engine, expire_on_commit=False) as session:
        first_interview = create_interview(session, invitation_token=first_token)
        second_interview = create_interview(session, invitation_token=second_token)
    first_identity = video_participant_identity(
        first_interview.id,
        VideoParticipantRole.CANDIDATE,
    )
    second_identity = video_participant_identity(
        second_interview.id,
        VideoParticipantRole.CANDIDATE,
    )
    fake_video_adapter.participants[video_room_name(first_interview.id)] = [
        VideoParticipant(first_identity)
    ]

    with authenticated_client(application) as client:
        room = client.get(
            f"/interviews/join/{first_token}",
            params={
                "room": video_room_name(second_interview.id),
                "identity": second_identity,
            },
        )
        lifecycle = client.post(
            f"/interviews/join/{first_token}/lifecycle-sync",
            json={"interview_id": str(second_interview.id)},
        )

    assert room.status_code == 200
    assert first_identity in room.text
    assert second_identity not in room.text
    assert lifecycle.status_code == 200
    assert lifecycle.json()["interview_id"] == str(first_interview.id)


def test_interviewer_end_api_persists_and_closes_provider_room(
    application: FastAPI,
    database_engine: Engine,
    fake_video_adapter: FakeVideoAdapter,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session)
        interview.status = InterviewStatus.WAITING
        session.commit()

    with authenticated_client(application) as client:
        response = client.post(f"/api/v1/interviews/{interview.id}/end")
        ended_room = client.get(f"/interviews/{interview.id}/room")

    assert response.status_code == 200
    assert response.json()["status"] == "ENDED"
    assert response.json()["ended_at"] is not None
    assert fake_video_adapter.terminated_rooms == [video_room_name(interview.id)]
    assert '"participant_token": null' in ended_room.text
    assert "This interview has ended." in ended_room.text


def test_custom_signalling_websocket_is_not_registered(application: FastAPI) -> None:
    websocket_paths = {
        route.path for route in application.routes if isinstance(route, APIWebSocketRoute)
    }
    assert "/ws/interviews/{interview_id}/signal" not in websocket_paths


def test_room_client_restores_and_separates_remote_media_tracks() -> None:
    client_source = (Path("app/static/js/interview_room.js")).read_text(encoding="utf-8")

    assert "attachAvailableRemoteTracks" in client_source
    assert "TrackPublished" in client_source
    assert "TrackSubscriptionFailed" in client_source
    assert "function attachRemoteTrack(track, _publication, participant)" in client_source
    assert "track.attach(remoteVideo)" in client_source
    assert "track.attach(remoteAudio)" in client_source


def test_interview_room_has_responsive_media_and_explicit_cursor_states() -> None:
    css_source = Path("app/static/css/styles.css").read_text(encoding="utf-8")
    client_source = Path("app/static/js/interview_room.js").read_text(encoding="utf-8")

    assert "aspect-ratio: 16 / 9" in css_source
    assert "object-fit: contain" in css_source
    assert "@media (max-width: 48rem)" in css_source
    assert "cursor: not-allowed" in css_source
    assert 'button[aria-busy="true"]' in css_source
    assert "cursor: progress" in css_source
    assert "cursor: wait" not in css_source
    assert "setButtonLoading(joinButton, true)" in client_source
    assert "setButtonLoading(joinButton, false)" in client_source


def test_interviewer_transcript_uses_current_text_stream_api() -> None:
    client_source = Path("app/static/js/interview_room.js").read_text(encoding="utf-8")

    assert 'registerTextStreamHandler("lk.transcription"' in client_source
    assert 'attributes["lk.transcription_final"]' in client_source
    assert 'attributes["lk.segment_id"]' in client_source
    assert 'attributes["lk.transcribed_track_id"]' in client_source
    assert "TranscriptionReceived" not in client_source
    assert "publish_transcription" not in client_source


@pytest.mark.asyncio
async def test_recruiter_room_dispatches_transcriber_with_public_fallback(
    db_session: Session,
    fake_video_adapter: FakeVideoAdapter,
) -> None:
    interview = create_interview(db_session)
    service = InterviewRoomService(
        InterviewRepository(db_session),
        JobRepository(db_session),
        fake_video_adapter,
        app_base_url="https://recrunion.example.test",
        transcription_agent_name="recrunion-transcriber",
        transcription_dispatch_enabled=True,
        transcription_ingest_configured=True,
    )

    first = await service.get_interviewer_room(interview.id)
    second = await service.get_interviewer_room(interview.id)

    assert first.transcription_status == "STARTING"
    assert second.transcription_status == "STARTING"
    assert len(fake_video_adapter.transcription_dispatches) == 1
    dispatch = fake_video_adapter.transcription_dispatches[0]
    assert dispatch["room"] == video_room_name(interview.id)
    assert dispatch["agent_name"] == "recrunion-transcriber"
    metadata = json.loads(dispatch["metadata"])
    assert metadata == {
        "callback_url": (
            "https://recrunion.example.test/api/v1/internal/interviews/"
            f"{interview.id}/transcript-segments"
        ),
        "interview_id": str(interview.id),
    }
    assert "secret" not in dispatch["metadata"].casefold()


@pytest.mark.asyncio
async def test_private_callback_base_overrides_public_app_base(
    db_session: Session,
    fake_video_adapter: FakeVideoAdapter,
) -> None:
    interview = create_interview(db_session)
    service = InterviewRoomService(
        InterviewRepository(db_session),
        JobRepository(db_session),
        fake_video_adapter,
        app_base_url="https://public-recrunion.example.test",
        transcription_callback_base_url="http://app:8000",
        transcription_agent_name="recrunion-transcriber",
        transcription_dispatch_enabled=True,
        transcription_ingest_configured=True,
    )

    room = await service.get_interviewer_room(interview.id)

    assert room.transcription_status == "STARTING"
    metadata = json.loads(fake_video_adapter.transcription_dispatches[0]["metadata"])
    assert metadata["callback_url"] == (
        f"http://app:8000/api/v1/internal/interviews/{interview.id}/transcript-segments"
    )
    assert "public-recrunion" not in fake_video_adapter.transcription_dispatches[0]["metadata"]
    assert "secret" not in fake_video_adapter.transcription_dispatches[0]["metadata"].casefold()


@pytest.mark.asyncio
async def test_transcription_dispatch_failure_does_not_break_interviewer_room(
    db_session: Session,
    fake_video_adapter: FakeVideoAdapter,
) -> None:
    interview = create_interview(db_session)
    fake_video_adapter.dispatch_error = VideoProviderError("raw provider detail")
    service = InterviewRoomService(
        InterviewRepository(db_session),
        JobRepository(db_session),
        fake_video_adapter,
        app_base_url="https://recrunion.example.test",
        transcription_agent_name="recrunion-transcriber",
        transcription_dispatch_enabled=True,
        transcription_ingest_configured=True,
    )

    room = await service.get_interviewer_room(interview.id)

    assert room.join_allowed is True
    assert room.participant_token == "synthetic-livekit-participant-token"
    assert room.transcription_status == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_transcription_dispatch_requires_public_https_callback(
    db_session: Session,
    fake_video_adapter: FakeVideoAdapter,
) -> None:
    interview = create_interview(db_session)
    service = InterviewRoomService(
        InterviewRepository(db_session),
        JobRepository(db_session),
        fake_video_adapter,
        app_base_url="http://localhost:8000",
        transcription_agent_name="recrunion-transcriber",
        transcription_dispatch_enabled=True,
        transcription_ingest_configured=True,
    )

    room = await service.get_interviewer_room(interview.id)

    assert room.transcription_status == "UNAVAILABLE"
    assert fake_video_adapter.transcription_dispatches == []


def test_compose_runs_transcription_agent_without_public_port() -> None:
    compose = Path(__file__).parents[1].joinpath("docker-compose.yml").read_text(encoding="utf-8")
    agent_service = compose.split("  transcription-agent:", 1)[1].split("  postgres:", 1)[0]

    assert "context: ./livekit_agent" in agent_service
    assert "ASSEMBLYAI_API_KEY: ${ASSEMBLYAI_API_KEY:-}" in agent_service
    assert "STT_AGENT_INGEST_SECRET: ${STT_AGENT_INGEST_SECRET:-}" in agent_service
    assert "restart: unless-stopped" in agent_service
    assert "ports:" not in agent_service
