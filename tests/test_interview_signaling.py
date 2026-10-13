from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.interviews import InterviewSession, InterviewStatus, InvitationStatus
from app.schemas.interview_signaling import InterviewParticipantRole
from app.services.interview_rooms import SignalingTicketCodec
from app.services.interview_signaling import SignalingHub
from tests.test_interview_scheduling import TEST_SECRET, create_scored_application


def create_interview(session: Session) -> InterviewSession:
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
        candidate_access_token_hash=uuid4().hex + uuid4().hex,
        invitation_email="candidate@example.com",
        invitation_status=InvitationStatus.SENT,
    )
    session.add(interview)
    session.commit()
    return interview


def ticket(interview_id: UUID, role: InterviewParticipantRole) -> str:
    return SignalingTicketCodec(TEST_SECRET, 600).issue(interview_id, role)


@pytest.fixture
def configured_application(application: FastAPI):
    settings = get_settings()
    original_secret = settings.jwt_secret
    original_ttl = settings.webrtc_signaling_ticket_ttl_seconds
    settings.jwt_secret = TEST_SECRET
    settings.webrtc_signaling_ticket_ttl_seconds = 600
    try:
        yield application
    finally:
        settings.jwt_secret = original_secret
        settings.webrtc_signaling_ticket_ttl_seconds = original_ttl


def test_websocket_authentication_and_offer_answer_ice_relay(
    configured_application: FastAPI,
    database_engine: Engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session)
        interview_id = interview.id

    path = f"/ws/interviews/{interview_id}/signal"
    with (
        TestClient(configured_application) as client,
        client.websocket_connect(path) as interviewer,
    ):
        interviewer.send_json(
            {
                "type": "authenticate",
                "ticket": ticket(interview_id, InterviewParticipantRole.INTERVIEWER),
            }
        )
        assert interviewer.receive_json() == {
            "type": "authenticated",
            "role": "INTERVIEWER",
            "offerer": True,
            "peer_present": False,
        }
        with client.websocket_connect(path) as candidate:
            candidate.send_json(
                {
                    "type": "authenticate",
                    "ticket": ticket(interview_id, InterviewParticipantRole.CANDIDATE),
                }
            )
            assert candidate.receive_json()["type"] == "authenticated"
            assert interviewer.receive_json() == {
                "type": "peer-joined",
                "role": "CANDIDATE",
            }
            assert candidate.receive_json() == {
                "type": "peer-joined",
                "role": "INTERVIEWER",
            }

            interviewer.send_json({"type": "offer", "sdp": "safe-offer-sdp"})
            assert candidate.receive_json() == {
                "type": "offer",
                "sdp": "safe-offer-sdp",
            }
            candidate.send_json({"type": "answer", "sdp": "safe-answer-sdp"})
            assert interviewer.receive_json() == {
                "type": "answer",
                "sdp": "safe-answer-sdp",
            }
            candidate.send_json(
                {
                    "type": "ice-candidate",
                    "candidate": {
                        "candidate": "candidate:synthetic",
                        "sdpMid": "0",
                        "sdpMLineIndex": 0,
                    },
                }
            )
            ice = interviewer.receive_json()
            assert ice["type"] == "ice-candidate"
            assert ice["candidate"]["candidate"] == "candidate:synthetic"

            candidate.send_json({"type": "ice-restart-request"})
            assert interviewer.receive_json() == {"type": "ice-restart-request"}

            candidate.send_json({"type": "leave"})
            assert interviewer.receive_json() == {
                "type": "peer-left",
                "role": "CANDIDATE",
            }


def test_websocket_rejects_invalid_wrong_scope_and_expired_tickets(
    configured_application: FastAPI,
    database_engine: Engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session)
        other = create_interview(session)

    cases = [
        "invalid-ticket",
        ticket(other.id, InterviewParticipantRole.CANDIDATE),
        SignalingTicketCodec(TEST_SECRET, 30).issue(
            interview.id,
            InterviewParticipantRole.CANDIDATE,
            now=datetime.now(UTC) - timedelta(minutes=2),
        ),
    ]
    with TestClient(configured_application) as client:
        for supplied_ticket in cases:
            with client.websocket_connect(f"/ws/interviews/{interview.id}/signal") as socket:
                socket.send_json({"type": "authenticate", "ticket": supplied_ticket})
                assert socket.receive_json()["code"] == "AUTHENTICATION_FAILED"


def test_ended_interview_rejects_signaling_and_renders_read_only_room(
    configured_application: FastAPI,
    database_engine: Engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session)
        interview.status = InterviewStatus.ENDED
        interview.ended_at = datetime.now(UTC)
        session.commit()

    path = f"/ws/interviews/{interview.id}/signal"
    with TestClient(configured_application) as client:
        with client.websocket_connect(path) as socket:
            socket.send_json(
                {
                    "type": "authenticate",
                    "ticket": ticket(interview.id, InterviewParticipantRole.CANDIDATE),
                }
            )
            assert socket.receive_json()["code"] == "INTERVIEW_ENDED"

        room = client.get(f"/interviews/{interview.id}/room")

    assert room.status_code == 200
    assert "This interview has ended." in room.text
    assert '"signaling_ticket": null' in room.text
    assert '"ice_servers": []' in room.text


def test_websocket_rejects_duplicate_role_and_role_violation(
    configured_application: FastAPI,
    database_engine: Engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session)
    path = f"/ws/interviews/{interview.id}/signal"

    with (
        TestClient(configured_application) as client,
        client.websocket_connect(path) as first,
    ):
        first.send_json(
            {
                "type": "authenticate",
                "ticket": ticket(interview.id, InterviewParticipantRole.CANDIDATE),
            }
        )
        assert first.receive_json()["type"] == "authenticated"
        with client.websocket_connect(path) as duplicate:
            duplicate.send_json(
                {
                    "type": "authenticate",
                    "ticket": ticket(
                        interview.id,
                        InterviewParticipantRole.CANDIDATE,
                    ),
                }
            )
            assert duplicate.receive_json()["code"] == "ROLE_ALREADY_CONNECTED"

        first.send_json({"type": "offer", "sdp": "not-allowed"})
        assert first.receive_json()["code"] == "ACTION_NOT_ALLOWED"


def test_websocket_rejects_malformed_and_unsupported_messages(
    configured_application: FastAPI,
    database_engine: Engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session)
    path = f"/ws/interviews/{interview.id}/signal"

    with TestClient(configured_application) as client:
        with client.websocket_connect(path) as socket:
            socket.send_text("not-json")
            assert socket.receive_json()["code"] == "INVALID_MESSAGE"

        with client.websocket_connect(path) as socket:
            socket.send_json({"type": "unknown", "ticket": "not-used"})
            assert socket.receive_json()["code"] == "INVALID_MESSAGE"

        with client.websocket_connect(path) as socket:
            socket.send_text("x" * 131_073)
            assert socket.receive_json()["code"] == "MESSAGE_TOO_LARGE"


def test_interviewer_end_call_notifies_both_participants(
    configured_application: FastAPI,
    database_engine: Engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session)
    path = f"/ws/interviews/{interview.id}/signal"

    with (
        TestClient(configured_application) as client,
        client.websocket_connect(path) as interviewer,
    ):
        interviewer.send_json(
            {
                "type": "authenticate",
                "ticket": ticket(interview.id, InterviewParticipantRole.INTERVIEWER),
            }
        )
        interviewer.receive_json()
        with client.websocket_connect(path) as candidate:
            candidate.send_json(
                {
                    "type": "authenticate",
                    "ticket": ticket(interview.id, InterviewParticipantRole.CANDIDATE),
                }
            )
            candidate.receive_json()
            interviewer.receive_json()
            candidate.receive_json()

            interviewer.send_json({"type": "end-call"})
            assert interviewer.receive_json() == {
                "type": "call-ended",
                "ended_by": "INTERVIEWER",
            }
            assert candidate.receive_json() == {
                "type": "call-ended",
                "ended_by": "INTERVIEWER",
            }

    with Session(database_engine) as session:
        persisted = session.get(InterviewSession, interview.id)
        assert persisted is not None
        assert persisted.status == InterviewStatus.ENDED
        assert persisted.ended_at is not None


def test_connected_peers_activate_once_and_candidate_can_reconnect(
    configured_application: FastAPI,
    database_engine: Engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session)
    path = f"/ws/interviews/{interview.id}/signal"

    with (
        TestClient(configured_application) as client,
        client.websocket_connect(path) as interviewer,
    ):
        interviewer.send_json(
            {
                "type": "authenticate",
                "ticket": ticket(interview.id, InterviewParticipantRole.INTERVIEWER),
            }
        )
        interviewer.receive_json()
        with client.websocket_connect(path) as candidate:
            candidate.send_json(
                {
                    "type": "authenticate",
                    "ticket": ticket(interview.id, InterviewParticipantRole.CANDIDATE),
                }
            )
            candidate.receive_json()
            interviewer.receive_json()
            candidate.receive_json()
            interviewer.send_json({"type": "peer-connected"})
            candidate.send_json({"type": "peer-connected"})
            assert interviewer.receive_json()["type"] == "interview-active"
            assert candidate.receive_json()["type"] == "interview-active"
            candidate.send_json({"type": "leave"})
            assert interviewer.receive_json()["type"] == "peer-left"

        with client.websocket_connect(path) as reconnected:
            reconnected.send_json(
                {
                    "type": "authenticate",
                    "ticket": ticket(interview.id, InterviewParticipantRole.CANDIDATE),
                }
            )
            assert reconnected.receive_json()["type"] == "authenticated"
            assert interviewer.receive_json()["type"] == "peer-joined"
            assert reconnected.receive_json()["type"] == "peer-joined"

    with Session(database_engine) as session:
        persisted = session.get(InterviewSession, interview.id)
        assert persisted is not None
        assert persisted.status == InterviewStatus.ACTIVE
        assert persisted.started_at is not None


def test_recruiter_and_candidate_room_pages_render_safe_config(
    configured_application: FastAPI,
    database_engine: Engine,
) -> None:
    from app.services.interview_scheduling import hash_token

    invitation_token = "synthetic-private-invitation-token"
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session)
        interview.candidate_access_token_hash = hash_token(invitation_token)
        session.commit()

    with TestClient(configured_application) as client:
        recruiter = client.get(f"/interviews/{interview.id}/room")
        candidate = client.get(f"/interviews/join/{invitation_token}")

    assert recruiter.status_code == 200
    assert candidate.status_code == 200
    assert "Check camera &amp; microphone" in candidate.text
    assert "interview_room.js" in candidate.text
    assert '"role": "CANDIDATE"' in candidate.text
    assert '"role": "INTERVIEWER"' in recruiter.text
    assert "Suitability Score" not in candidate.text


class RecordingSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_json(self, message: dict[str, object]) -> None:
        self.messages.append(message)


@pytest.mark.asyncio
async def test_signaling_hub_isolates_rooms_and_cleans_empty_rooms() -> None:
    hub = SignalingHub()
    first_id, second_id = uuid4(), uuid4()
    first_interviewer = RecordingSocket()
    first_candidate = RecordingSocket()
    second_candidate = RecordingSocket()

    await hub.join(
        first_id,
        InterviewParticipantRole.INTERVIEWER,
        first_interviewer,  # type: ignore[arg-type]
    )
    await hub.join(
        first_id,
        InterviewParticipantRole.CANDIDATE,
        first_candidate,  # type: ignore[arg-type]
    )
    await hub.join(
        second_id,
        InterviewParticipantRole.CANDIDATE,
        second_candidate,  # type: ignore[arg-type]
    )
    await hub.relay_to_peer(
        first_id,
        InterviewParticipantRole.INTERVIEWER,
        {"type": "offer", "sdp": "room-one"},
    )

    assert first_candidate.messages[-1]["sdp"] == "room-one"
    assert all("sdp" not in message for message in second_candidate.messages)

    await hub.leave(
        first_id,
        InterviewParticipantRole.INTERVIEWER,
        first_interviewer,  # type: ignore[arg-type]
    )
    await hub.leave(
        first_id,
        InterviewParticipantRole.CANDIDATE,
        first_candidate,  # type: ignore[arg-type]
    )
    assert await hub.participant_count(first_id) == 0
    assert await hub.participant_count(second_id) == 1
