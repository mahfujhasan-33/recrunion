from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.adapters.video import (
    VideoParticipant,
    VideoParticipantRole,
    video_participant_identity,
    video_room_name,
)
from app.errors import (
    InterviewLifecycleError,
    InterviewNotFoundError,
    InterviewProviderCleanupError,
    VideoProviderError,
)
from app.models.interviews import InterviewSession, InterviewStatus
from app.repositories.interviews import InterviewRepository
from app.repositories.jobs import JobRepository
from app.services.interview_rooms import InterviewRoomService
from tests.conftest import FakeVideoAdapter
from tests.test_interview_scheduling import (
    FakeEmailAdapter,
    build_service,
    create_scored_application,
    future_payload,
)


def build_room_service(
    session: Session,
    video_adapter: FakeVideoAdapter | None = None,
) -> InterviewRoomService:
    return InterviewRoomService(
        InterviewRepository(session),
        JobRepository(session),
        video_adapter or FakeVideoAdapter(),
    )


@pytest.mark.asyncio
async def test_candidate_and_interviewer_rooms_receive_server_selected_credentials(
    db_session: Session,
) -> None:
    _, application = create_scored_application(db_session)
    scheduled = await build_service(db_session, FakeEmailAdapter()).schedule(
        application.id,
        future_payload(),
    )
    invitation_token = scheduled.candidate_join_url.rsplit("/", 1)[-1]
    service = build_room_service(db_session)

    candidate = service.get_candidate_room(invitation_token)
    interviewer = await service.get_interviewer_room(scheduled.id)

    assert candidate.role == VideoParticipantRole.CANDIDATE
    assert candidate.candidate_reference is None
    assert interviewer.role == VideoParticipantRole.INTERVIEWER
    assert interviewer.candidate_reference == application.candidate.display_reference
    assert candidate.livekit_url == "wss://synthetic.livekit.example.test"
    assert candidate.participant_token == "synthetic-livekit-participant-token"
    assert candidate.participant_identity == video_participant_identity(
        scheduled.id,
        VideoParticipantRole.CANDIDATE,
    )
    assert interviewer.participant_identity == video_participant_identity(
        scheduled.id,
        VideoParticipantRole.INTERVIEWER,
    )
    assert candidate.lifecycle_sync_url.endswith(f"/{invitation_token}/lifecycle-sync")
    assert candidate.end_url is None
    assert interviewer.end_url == f"/api/v1/interviews/{scheduled.id}/end"


@pytest.mark.asyncio
async def test_missing_provider_configuration_keeps_room_safe_and_tokenless(
    db_session: Session,
) -> None:
    _, application = create_scored_application(db_session)
    scheduled = await build_service(db_session, FakeEmailAdapter()).schedule(
        application.id,
        future_payload(),
    )
    service = build_room_service(db_session, FakeVideoAdapter(configured=False))

    room = await service.get_interviewer_room(scheduled.id)

    assert room.provider_configured is False
    assert room.join_allowed is False
    assert room.participant_token is None
    assert room.livekit_url is None
    assert room.provider_error_message is not None


@pytest.mark.asyncio
async def test_room_lookup_rejects_unknown_invitation_and_interview(db_session: Session) -> None:
    service = build_room_service(db_session)

    with pytest.raises(InterviewNotFoundError):
        service.get_candidate_room("not-a-valid-invitation")
    with pytest.raises(InterviewNotFoundError):
        await service.get_interviewer_room(uuid4())


@pytest.mark.asyncio
async def test_provider_verified_presence_preserves_lifecycle_and_started_at(
    db_session: Session,
) -> None:
    _, application = create_scored_application(db_session)
    scheduled = await build_service(db_session, FakeEmailAdapter()).schedule(
        application.id,
        future_payload(),
    )
    adapter = FakeVideoAdapter()
    service = build_room_service(db_session, adapter)
    room_name = video_room_name(scheduled.id)
    candidate_identity = video_participant_identity(
        scheduled.id,
        VideoParticipantRole.CANDIDATE,
    )
    interviewer_identity = video_participant_identity(
        scheduled.id,
        VideoParticipantRole.INTERVIEWER,
    )

    adapter.participants[room_name] = [VideoParticipant(candidate_identity)]
    waiting = await service.sync_interviewer_lifecycle(scheduled.id)
    assert waiting.status == InterviewStatus.WAITING
    assert waiting.started_at is None

    adapter.participants[room_name] = [
        VideoParticipant(candidate_identity),
        VideoParticipant(interviewer_identity),
        VideoParticipant(f"agent_{scheduled.id.hex}"),
    ]
    active = await service.sync_interviewer_lifecycle(scheduled.id)
    assert active.status == InterviewStatus.ACTIVE
    assert active.started_at is not None
    started_at = active.started_at

    adapter.participants[room_name] = [VideoParticipant(interviewer_identity)]
    reconnected = await service.sync_interviewer_lifecycle(scheduled.id)
    assert reconnected.status == InterviewStatus.ACTIVE
    assert reconnected.started_at == started_at


@pytest.mark.asyncio
async def test_service_participant_does_not_activate_interview(db_session: Session) -> None:
    _, application = create_scored_application(db_session)
    scheduled = await build_service(db_session, FakeEmailAdapter()).schedule(
        application.id,
        future_payload(),
    )
    adapter = FakeVideoAdapter()
    adapter.participants[video_room_name(scheduled.id)] = [
        VideoParticipant(f"agent_{scheduled.id.hex}")
    ]

    unchanged = await build_room_service(db_session, adapter).sync_interviewer_lifecycle(
        scheduled.id
    )

    assert unchanged.status == InterviewStatus.SCHEDULED
    assert unchanged.started_at is None


@pytest.mark.asyncio
async def test_interviewer_end_is_write_once_and_terminates_provider_room(
    db_session: Session,
) -> None:
    _, application = create_scored_application(db_session)
    scheduled = await build_service(db_session, FakeEmailAdapter()).schedule(
        application.id,
        future_payload(),
    )
    adapter = FakeVideoAdapter()
    service = build_room_service(db_session, adapter)
    adapter.participants[video_room_name(scheduled.id)] = [
        VideoParticipant(video_participant_identity(scheduled.id, VideoParticipantRole.CANDIDATE))
    ]
    await service.sync_interviewer_lifecycle(scheduled.id)
    ended_at = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)

    with pytest.raises(InterviewLifecycleError, match="Only the interviewer"):
        await service.end_interview(scheduled.id, VideoParticipantRole.CANDIDATE)

    ended = await service.end_interview(
        scheduled.id,
        VideoParticipantRole.INTERVIEWER,
        now=ended_at,
    )
    ended_again = await service.end_interview(
        scheduled.id,
        VideoParticipantRole.INTERVIEWER,
        now=ended_at + timedelta(minutes=1),
    )

    assert ended.status == InterviewStatus.ENDED
    assert ended_again.ended_at == ended.ended_at
    assert ended_again.ended_at is not None
    assert adapter.terminated_rooms == [video_room_name(scheduled.id)]
    assert (await service.get_interviewer_room(scheduled.id)).participant_token is None
    assert (await service.sync_interviewer_lifecycle(scheduled.id)).status == InterviewStatus.ENDED


@pytest.mark.asyncio
async def test_provider_cleanup_failure_does_not_reactivate_ended_interview(
    db_session: Session,
) -> None:
    _, application = create_scored_application(db_session)
    scheduled = await build_service(db_session, FakeEmailAdapter()).schedule(
        application.id,
        future_payload(),
    )
    adapter = FakeVideoAdapter()
    service = build_room_service(db_session, adapter)
    adapter.participants[video_room_name(scheduled.id)] = [
        VideoParticipant(video_participant_identity(scheduled.id, VideoParticipantRole.INTERVIEWER))
    ]
    await service.sync_interviewer_lifecycle(scheduled.id)
    adapter.terminate_error = VideoProviderError("synthetic provider failure")

    with pytest.raises(InterviewProviderCleanupError):
        await service.end_interview(scheduled.id, VideoParticipantRole.INTERVIEWER)

    persisted = db_session.get(InterviewSession, scheduled.id)
    assert persisted is not None
    assert persisted.status == InterviewStatus.ENDED
    assert persisted.ended_at is not None
