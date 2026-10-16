from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.interview_probes import (
    AssistanceKind,
    InterviewAssistanceRun,
    InterviewProbe,
    ProbeStatus,
)
from app.models.processing_jobs import ProcessingJob


class InterviewProbeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def runs(self, interview_id: UUID) -> list[InterviewAssistanceRun]:
        return list(
            self._session.scalars(
                select(InterviewAssistanceRun)
                .where(InterviewAssistanceRun.interview_id == interview_id)
                .order_by(InterviewAssistanceRun.created_at.desc(), InterviewAssistanceRun.id)
            )
        )

    def get_run(self, run_id: UUID) -> InterviewAssistanceRun | None:
        return self._session.get(InterviewAssistanceRun, run_id)

    def refresh_context(self) -> None:
        """Re-read sources after a provider wait instead of trusting the identity map."""
        self._session.expire_all()

    def task(self, run: InterviewAssistanceRun) -> ProcessingJob:
        return self._session.get(ProcessingJob, run.task_id)

    def probes(self, interview_id: UUID) -> list[InterviewProbe]:
        return list(
            self._session.scalars(
                select(InterviewProbe)
                .join(InterviewAssistanceRun)
                .where(InterviewAssistanceRun.interview_id == interview_id)
                .order_by(
                    InterviewProbe.created_at.desc(), InterviewProbe.priority, InterviewProbe.id
                )
            )
        )

    def queue(self, run: InterviewAssistanceRun, task: ProcessingJob) -> None:
        self._session.add(task)
        self._session.flush()
        self._session.add(run)
        self._commit()

    def complete(
        self, run: InterviewAssistanceRun, probes: list[InterviewProbe], response: dict | None
    ) -> None:
        # Only replace unused suggestions, never recruiter interaction history.
        if run.kind == AssistanceKind.PROBES:
            run_ids = select(InterviewAssistanceRun.id).where(
                InterviewAssistanceRun.interview_id == run.interview_id
            )
            self._session.execute(
                delete(InterviewProbe).where(
                    InterviewProbe.run_id.in_(run_ids), InterviewProbe.status == ProbeStatus.READY
                )
            )
            self._session.add_all(probes)
        run.response = response
        self._commit()

    def save_status(self, probe: InterviewProbe, status: ProbeStatus) -> None:
        probe.status = status
        self._commit()

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
