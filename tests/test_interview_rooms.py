from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.errors import (
    InterviewConfigurationError,
    InterviewEndedError,
    InterviewLifecycleError,
    InterviewNotFoundError,
)
from app.models.interviews import InterviewStatus
from app.repositories.interviews import InterviewRepository
from app.repositories.jobs import JobRepository
from app.schemas.interview_signaling import InterviewParticipantRole
from app.services.interview_rooms import (
    InterviewRoomService,
    SignalingTicketCodec,
    SignalingTicketError,
    build_ice_server_configuration,
    parse_stun_urls,
    parse_turn_urls,
)
from tests.test_interview_scheduling import (
    TEST_SECRET,
    FakeEmailAdapter,
    build_service,
    create_scored_application,
    future_payload,
)


def build_room_service(session: Session) -> InterviewRoomService:
    return InterviewRoomService(
        InterviewRepository(session),
        JobRepository(session),
        token_secret=TEST_SECRET,
        signaling_ticket_ttl_seconds=600,
        stun_urls=["stun:stun.example.test:3478"],
    )


@pytest.mark.asyncio
async def test_candidate_and_interviewer_rooms_issue_role_scoped_tickets(
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
    interviewer = service.get_interviewer_room(scheduled.id)

    assert candidate.role == InterviewParticipantRole.CANDIDATE
    assert candidate.candidate_reference is None
    assert interviewer.role == InterviewParticipantRole.INTERVIEWER
    assert interviewer.candidate_reference == application.candidate.display_reference
    assert candidate.ice_servers[0].urls == ["stun:stun.example.test:3478"]
    assert (
        service.authenticate_ticket(
            candidate.signaling_ticket,
            scheduled.id,
        ).role
        == InterviewParticipantRole.CANDIDATE
    )


@pytest.mark.asyncio
async def test_room_lookup_rejects_unknown_invitation_and_interview(
    db_session: Session,
) -> None:
    service = build_room_service(db_session)

    with pytest.raises(InterviewNotFoundError):
        service.get_candidate_room("not-a-valid-invitation")
    with pytest.raises(InterviewNotFoundError):
        service.get_interviewer_room(__import__("uuid").uuid4())


def test_signaling_ticket_rejects_expiry_scope_role_and_tampering() -> None:
    from uuid import uuid4

    interview_id = uuid4()
    now = datetime(2026, 10, 13, tzinfo=UTC)
    codec = SignalingTicketCodec(TEST_SECRET, 60)
    ticket = codec.issue(
        interview_id,
        InterviewParticipantRole.CANDIDATE,
        now=now,
    )

    claims = codec.verify(
        ticket,
        expected_interview_id=interview_id,
        expected_role=InterviewParticipantRole.CANDIDATE,
        now=now + timedelta(seconds=30),
    )
    assert claims.interview_id == interview_id

    with pytest.raises(SignalingTicketError, match="expired"):
        codec.verify(
            ticket,
            expected_interview_id=interview_id,
            now=now + timedelta(seconds=61),
        )
    with pytest.raises(SignalingTicketError, match="wrong interview"):
        codec.verify(ticket, expected_interview_id=uuid4(), now=now)
    with pytest.raises(SignalingTicketError, match="wrong participant role"):
        codec.verify(
            ticket,
            expected_interview_id=interview_id,
            expected_role=InterviewParticipantRole.INTERVIEWER,
            now=now,
        )
    with pytest.raises(SignalingTicketError, match="invalid"):
        codec.verify(
            f"{ticket[:-1]}x",
            expected_interview_id=interview_id,
            now=now,
        )


def test_stun_configuration_ignores_blank_entries() -> None:
    assert parse_stun_urls(" stun:one.example , ,stun:two.example ") == [
        "stun:one.example",
        "stun:two.example",
    ]


def test_turn_configuration_is_normalized_without_affecting_stun_only() -> None:
    stun_only = build_ice_server_configuration(
        ["stun:stun.example.test:3478"],
        [],
        "",
        "",
        "all",
    )
    assert len(stun_only) == 1
    assert stun_only[0].username is None

    turn_urls = parse_turn_urls(
        "turn:turn.example.test:3478?transport=udp, turns:turn.example.test:5349?transport=tcp"
    )
    configured = build_ice_server_configuration(
        ["stun:stun.example.test:3478"],
        turn_urls,
        "demo-user",
        "private-demo-password",
        "relay",
    )

    assert configured[1].urls == turn_urls
    assert configured[1].username == "demo-user"
    assert configured[1].credential == "private-demo-password"


@pytest.mark.parametrize(
    ("turn_urls", "username", "password", "policy"),
    [
        (["turn:turn.example.test:3478"], "", "", "all"),
        ([], "demo-user", "", "all"),
        ([], "demo-user", "private-demo-password", "all"),
        ([], "", "", "relay"),
    ],
)
def test_incomplete_turn_configuration_fails_safely(
    turn_urls: list[str],
    username: str,
    password: str,
    policy: str,
) -> None:
    with pytest.raises(InterviewConfigurationError):
        build_ice_server_configuration([], turn_urls, username, password, policy)

    with pytest.raises(InterviewConfigurationError):
        parse_turn_urls("https://not-a-turn-server.example.test")


@pytest.mark.asyncio
async def test_interview_lifecycle_is_persisted_and_timestamps_are_write_once(
    db_session: Session,
) -> None:
    _, application = create_scored_application(db_session)
    scheduled = await build_service(db_session, FakeEmailAdapter()).schedule(
        application.id,
        future_payload(),
    )
    service = build_room_service(db_session)
    ticket = service.issue_ticket(scheduled.id, InterviewParticipantRole.CANDIDATE)
    started_at = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)
    later = started_at + timedelta(minutes=30)

    assert service.participant_joined(scheduled.id) == InterviewStatus.WAITING
    started = service.mark_active(scheduled.id, now=started_at)
    restarted = service.mark_active(scheduled.id, now=later)

    assert started.status == InterviewStatus.ACTIVE
    assert restarted.started_at == started.started_at
    with pytest.raises(InterviewLifecycleError, match="Only the interviewer"):
        service.end_interview(scheduled.id, InterviewParticipantRole.CANDIDATE)

    ended = service.end_interview(
        scheduled.id,
        InterviewParticipantRole.INTERVIEWER,
        now=later,
    )
    ended_again = service.end_interview(
        scheduled.id,
        InterviewParticipantRole.INTERVIEWER,
        now=later + timedelta(minutes=1),
    )

    assert ended.status == InterviewStatus.ENDED
    assert ended_again.ended_at == ended.ended_at
    assert service.get_interviewer_room(scheduled.id).join_allowed is False
    with pytest.raises(InterviewEndedError):
        service.authenticate_ticket(ticket, scheduled.id)
