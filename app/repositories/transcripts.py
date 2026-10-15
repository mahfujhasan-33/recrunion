from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.interviews import InterviewTranscriptSegment


class TranscriptRepository:
    """Persist and retrieve final interview transcript segments."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_key(
        self,
        interview_id: UUID,
        participant_identity: str,
        segment_key: str,
    ) -> InterviewTranscriptSegment | None:
        return self._session.scalar(
            select(InterviewTranscriptSegment).where(
                InterviewTranscriptSegment.interview_id == interview_id,
                InterviewTranscriptSegment.participant_identity == participant_identity,
                InterviewTranscriptSegment.segment_key == segment_key,
            )
        )

    def list_for_interview(self, interview_id: UUID) -> list[InterviewTranscriptSegment]:
        statement = (
            select(InterviewTranscriptSegment)
            .where(InterviewTranscriptSegment.interview_id == interview_id)
            .order_by(
                InterviewTranscriptSegment.started_at,
                InterviewTranscriptSegment.ended_at,
                InterviewTranscriptSegment.created_at,
                InterviewTranscriptSegment.id,
            )
        )
        return list(self._session.scalars(statement))

    def save(self, segment: InterviewTranscriptSegment) -> InterviewTranscriptSegment:
        self._session.add(segment)
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            raise
        except SQLAlchemyError:
            self._session.rollback()
            raise
        self._session.refresh(segment)
        return segment
