import hmac
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.adapters.video import VideoParticipantRole, video_participant_identity, video_room_name
from app.errors import (
    InterviewNotFoundError,
    TranscriptAuthenticationError,
    TranscriptValidationError,
)
from app.models.interviews import InterviewTranscriptSegment, TranscriptParticipantRole
from app.repositories.interviews import InterviewRepository
from app.repositories.transcripts import TranscriptRepository
from app.schemas.transcripts import (
    InterviewTranscriptResponse,
    TranscriptSegmentIngestRequest,
    TranscriptSegmentIngestResponse,
    TranscriptSegmentResponse,
)


@dataclass(frozen=True)
class TranscriptIngestCredentials:
    configured_secret: str

    def validate(self, authorization: str | None) -> None:
        prefix = "Bearer "
        if not self.configured_secret or not authorization or not authorization.startswith(prefix):
            raise TranscriptAuthenticationError
        supplied = authorization[len(prefix) :]
        if not hmac.compare_digest(supplied, self.configured_secret):
            raise TranscriptAuthenticationError


class TranscriptService:
    """Validate, persist, and retrieve normalized final transcript segments."""

    def __init__(
        self,
        interview_repository: InterviewRepository,
        transcript_repository: TranscriptRepository,
        credentials: TranscriptIngestCredentials,
    ) -> None:
        self._interviews = interview_repository
        self._transcripts = transcript_repository
        self._credentials = credentials

    def ingest(
        self,
        interview_id: UUID,
        payload: TranscriptSegmentIngestRequest,
        authorization: str | None,
    ) -> TranscriptSegmentIngestResponse:
        self._credentials.validate(authorization)
        interview = self._interviews.get(interview_id)
        if interview is None:
            raise InterviewNotFoundError
        if payload.room_name != video_room_name(interview_id):
            raise TranscriptValidationError("Transcript room does not match the interview.")
        role = self._derive_role(interview_id, payload.participant_identity)
        if payload.ended_at < payload.started_at:
            raise TranscriptValidationError("Transcript end time cannot precede its start time.")

        existing = self._transcripts.get_by_key(
            interview_id,
            payload.participant_identity,
            payload.segment_key,
        )
        if existing is not None:
            return TranscriptSegmentIngestResponse(
                segment=self._response(existing),
                created=False,
            )

        segment = InterviewTranscriptSegment(
            interview_id=interview_id,
            participant_role=role,
            participant_identity=payload.participant_identity,
            segment_key=payload.segment_key,
            started_at=payload.started_at,
            ended_at=payload.ended_at,
            text=payload.text,
            language=payload.language,
            provider=payload.provider,
            provider_segment_id=payload.provider_segment_id,
        )
        try:
            persisted = self._transcripts.save(segment)
        except IntegrityError:
            existing = self._transcripts.get_by_key(
                interview_id,
                payload.participant_identity,
                payload.segment_key,
            )
            if existing is None:
                raise
            return TranscriptSegmentIngestResponse(
                segment=self._response(existing),
                created=False,
            )
        return TranscriptSegmentIngestResponse(segment=self._response(persisted), created=True)

    def get_transcript(self, interview_id: UUID) -> InterviewTranscriptResponse:
        if self._interviews.get(interview_id) is None:
            raise InterviewNotFoundError
        return InterviewTranscriptResponse(
            interview_id=interview_id,
            segments=[
                self._response(segment)
                for segment in self._transcripts.list_for_interview(interview_id)
            ],
        )

    @staticmethod
    def _derive_role(interview_id: UUID, identity: str) -> TranscriptParticipantRole:
        if identity == video_participant_identity(interview_id, VideoParticipantRole.CANDIDATE):
            return TranscriptParticipantRole.CANDIDATE
        if identity == video_participant_identity(interview_id, VideoParticipantRole.INTERVIEWER):
            return TranscriptParticipantRole.INTERVIEWER
        raise TranscriptValidationError("Transcript publisher is not an expected participant.")

    @staticmethod
    def _response(segment: InterviewTranscriptSegment) -> TranscriptSegmentResponse:
        return TranscriptSegmentResponse(
            id=segment.id,
            participant_role=segment.participant_role,
            participant_identity=segment.participant_identity,
            segment_key=segment.segment_key,
            started_at=segment.started_at,
            ended_at=segment.ended_at,
            text=segment.text,
            language=segment.language,
            provider=segment.provider,
        )
