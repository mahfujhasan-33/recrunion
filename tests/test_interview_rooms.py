from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.errors import InterviewNotFoundError
from app.repositories.interviews import InterviewRepository
from app.repositories.jobs import JobRepository
from app.schemas.interview_signaling import InterviewParticipantRole
from app.services.interview_rooms import (
    InterviewRoomService,
    SignalingTicketCodec,
    SignalingTicketError,
    parse_stun_urls,
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
