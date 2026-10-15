from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.adapters.video import VideoParticipantRole, video_participant_identity, video_room_name
from app.dependencies import get_transcript_service
from app.errors import TranscriptAuthenticationError, TranscriptValidationError
from app.models.interviews import InterviewTranscriptSegment, TranscriptParticipantRole
from app.repositories.deletions import DeletionRepository
from app.repositories.interviews import InterviewRepository
from app.repositories.transcripts import TranscriptRepository
from app.schemas.transcripts import TranscriptSegmentIngestRequest
from app.services.transcripts import TranscriptIngestCredentials, TranscriptService
from tests.test_interview_livekit import create_interview

INGEST_SECRET = "synthetic-transcript-ingest-secret"


def build_service(session: Session) -> TranscriptService:
    return TranscriptService(
        InterviewRepository(session),
        TranscriptRepository(session),
        TranscriptIngestCredentials(INGEST_SECRET),
    )


def segment_payload(interview_id, *, key: str = "segment-1", text: str = "Hello"):
    started = datetime.now(UTC)
    return TranscriptSegmentIngestRequest(
        room_name=video_room_name(interview_id),
        participant_identity=video_participant_identity(
            interview_id,
            VideoParticipantRole.CANDIDATE,
        ),
        segment_key=key,
        started_at=started,
        ended_at=started + timedelta(seconds=2),
        text=text,
        language="en",
        provider="ASSEMBLYAI",
        provider_segment_id=key,
    )


def test_ingest_auth_identity_validation_and_idempotency(db_session: Session) -> None:
    interview = create_interview(db_session)
    service = build_service(db_session)
    payload = segment_payload(interview.id)

    with pytest.raises(TranscriptAuthenticationError):
        service.ingest(interview.id, payload, None)
    with pytest.raises(TranscriptAuthenticationError):
        service.ingest(interview.id, payload, "Bearer wrong")

    first = service.ingest(interview.id, payload, f"Bearer {INGEST_SECRET}")
    duplicate = service.ingest(interview.id, payload, f"Bearer {INGEST_SECRET}")

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.segment.id == first.segment.id
    assert first.segment.participant_role == TranscriptParticipantRole.CANDIDATE
    assert len(db_session.scalars(select(InterviewTranscriptSegment)).all()) == 1


def test_same_text_with_different_key_and_chronological_retrieval(db_session: Session) -> None:
    interview = create_interview(db_session)
    service = build_service(db_session)
    later = segment_payload(interview.id, key="later", text="Repeated answer")
    earlier = segment_payload(interview.id, key="earlier", text="Repeated answer").model_copy(
        update={
            "started_at": later.started_at - timedelta(seconds=10),
            "ended_at": later.ended_at - timedelta(seconds=10),
            "participant_identity": video_participant_identity(
                interview.id,
                VideoParticipantRole.INTERVIEWER,
            ),
        }
    )

    service.ingest(interview.id, later, f"Bearer {INGEST_SECRET}")
    service.ingest(interview.id, earlier, f"Bearer {INGEST_SECRET}")
    transcript = service.get_transcript(interview.id)

    assert [segment.segment_key for segment in transcript.segments] == ["earlier", "later"]
    assert [segment.participant_role for segment in transcript.segments] == [
        TranscriptParticipantRole.INTERVIEWER,
        TranscriptParticipantRole.CANDIDATE,
    ]


def test_ingest_rejects_wrong_room_publisher_blank_and_inverted_time(
    db_session: Session,
) -> None:
    interview = create_interview(db_session)
    service = build_service(db_session)
    payload = segment_payload(interview.id)
    authorization = f"Bearer {INGEST_SECRET}"

    with pytest.raises(TranscriptValidationError, match="room"):
        service.ingest(
            interview.id,
            payload.model_copy(update={"room_name": "another-room"}),
            authorization,
        )
    with pytest.raises(TranscriptValidationError, match="publisher"):
        service.ingest(
            interview.id,
            payload.model_copy(update={"participant_identity": "service_bot"}),
            authorization,
        )
    with pytest.raises(TranscriptValidationError, match="end time"):
        service.ingest(
            interview.id,
            payload.model_copy(update={"ended_at": payload.started_at - timedelta(seconds=1)}),
            authorization,
        )
    blank_payload = segment_payload(interview.id).model_dump()
    blank_payload["text"] = "   "
    with pytest.raises(ValueError, match="blank"):
        TranscriptSegmentIngestRequest.model_validate(blank_payload)


def test_transcript_api_accepts_authenticated_final_and_retrieves_it(
    application: FastAPI,
    database_engine: Engine,
) -> None:
    with Session(database_engine, expire_on_commit=False) as session:
        interview = create_interview(session)

    payload = segment_payload(interview.id)
    with Session(database_engine, expire_on_commit=False) as session:
        application.dependency_overrides[get_transcript_service] = lambda: build_service(session)
        with TestClient(application) as client:
            unauthorized = client.post(
                f"/api/v1/internal/interviews/{interview.id}/transcript-segments",
                json=payload.model_dump(mode="json"),
            )
            created = client.post(
                f"/api/v1/internal/interviews/{interview.id}/transcript-segments",
                json=payload.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {INGEST_SECRET}"},
            )
            duplicate = client.post(
                f"/api/v1/internal/interviews/{interview.id}/transcript-segments",
                json=payload.model_dump(mode="json"),
                headers={"Authorization": f"Bearer {INGEST_SECRET}"},
            )
            transcript = client.get(f"/api/v1/interviews/{interview.id}/transcript")

    assert unauthorized.status_code == 401
    assert created.status_code == 200
    assert created.json()["created"] is True
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert len(transcript.json()["segments"]) == 1


def test_candidate_room_does_not_render_transcript_panel(
    application: FastAPI,
    database_engine: Engine,
) -> None:
    token = "candidate-transcript-boundary-token"
    with Session(database_engine, expire_on_commit=False) as session:
        create_interview(session, invitation_token=token)

    with TestClient(application) as client:
        response = client.get(f"/interviews/join/{token}")

    assert response.status_code == 200
    assert 'id="interview-transcript-segments"' not in response.text
    assert 'id="transcription-status"' not in response.text


def test_application_deletion_removes_candidate_transcript(db_session: Session) -> None:
    interview = create_interview(db_session)
    service = build_service(db_session)
    service.ingest(
        interview.id,
        segment_payload(interview.id),
        f"Bearer {INGEST_SECRET}",
    )
    deletions = DeletionRepository(db_session)
    plan = deletions.plan_application_deletion(interview.job_id, interview.application_id)

    assert plan is not None
    deletions.delete_application(plan)

    assert db_session.scalars(select(InterviewTranscriptSegment)).all() == []
