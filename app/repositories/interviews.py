from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models.applications import CandidateDocument, JobApplication
from app.models.interviews import InterviewSession
from app.models.scoring import CandidateScore


class InterviewRepository:
    """Persist interview sessions and load their scheduling prerequisites."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_application(self, application_id: UUID) -> JobApplication | None:
        return self._session.scalar(
            select(JobApplication)
            .where(JobApplication.id == application_id)
            .options(
                selectinload(JobApplication.candidate),
                selectinload(JobApplication.document).selectinload(CandidateDocument.profile),
            )
        )

    def get_score(self, application_id: UUID) -> CandidateScore | None:
        return self._session.scalar(
            select(CandidateScore).where(CandidateScore.application_id == application_id)
        )

    def get(self, interview_id: UUID) -> InterviewSession | None:
        return self._session.get(InterviewSession, interview_id)

    def get_for_update(self, interview_id: UUID) -> InterviewSession | None:
        return self._session.scalar(
            select(InterviewSession).where(InterviewSession.id == interview_id).with_for_update()
        )

    def get_for_application(self, application_id: UUID) -> InterviewSession | None:
        return self._session.scalar(
            select(InterviewSession).where(InterviewSession.application_id == application_id)
        )

    def get_by_token_hash(self, token_hash: str) -> InterviewSession | None:
        return self._session.scalar(
            select(InterviewSession).where(
                InterviewSession.candidate_access_token_hash == token_hash
            )
        )

    def list_for_job(self, job_id: UUID) -> list[InterviewSession]:
        statement = (
            select(InterviewSession)
            .where(InterviewSession.job_id == job_id)
            .order_by(InterviewSession.scheduled_start_at, InterviewSession.id)
        )
        return list(self._session.scalars(statement))

    def save(self, interview: InterviewSession) -> InterviewSession:
        self._session.add(interview)
        self._commit()
        self._session.refresh(interview)
        return interview

    def rollback(self) -> None:
        self._session.rollback()

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
