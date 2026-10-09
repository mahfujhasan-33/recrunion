from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.scoring import CandidateScore, JobScoringConfig


class CandidateScoringRepository:
    """Persist job scoring configuration and one current score per application."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_config(self, job_id: UUID, *, lock: bool = False) -> JobScoringConfig | None:
        statement = select(JobScoringConfig).where(JobScoringConfig.job_id == job_id)
        if lock:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def save_config(self, config: JobScoringConfig) -> JobScoringConfig:
        self._session.add(config)
        self._commit()
        return self.get_config(config.job_id) or config

    def get_score(self, application_id: UUID, *, lock: bool = False) -> CandidateScore | None:
        statement = select(CandidateScore).where(CandidateScore.application_id == application_id)
        if lock:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_scores(self, job_id: UUID) -> list[CandidateScore]:
        statement = (
            select(CandidateScore)
            .where(CandidateScore.job_id == job_id)
            .order_by(CandidateScore.application_id)
        )
        return list(self._session.scalars(statement))

    def save_score(self, score: CandidateScore) -> CandidateScore:
        self._session.add(score)
        self._commit()
        return self.get_score(score.application_id) or score

    def rollback(self) -> None:
        self._session.rollback()

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
