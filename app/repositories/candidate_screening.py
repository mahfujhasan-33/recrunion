from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models.applications import CandidateDocument, CandidateProfile, JobApplication
from app.models.jobs import Job
from app.models.processing_jobs import ProcessingJob
from app.models.screening import (
    CandidateRequirementEvidence,
    CandidateRequirementMatch,
    CandidateScreening,
    CandidateScreeningStatus,
)


@dataclass(frozen=True)
class CandidateScreeningContext:
    screening: CandidateScreening
    job: Job
    application: JobApplication
    document: CandidateDocument
    profile: CandidateProfile


class CandidateScreeningRepository:
    """Persist screening aggregates and load candidate-scoped screening context."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lock_application(self, job_id: UUID, application_id: UUID) -> JobApplication | None:
        statement = (
            select(JobApplication)
            .where(JobApplication.job_id == job_id, JobApplication.id == application_id)
            .options(
                selectinload(JobApplication.candidate),
                selectinload(JobApplication.document).selectinload(CandidateDocument.profile),
                selectinload(JobApplication.document).selectinload(CandidateDocument.chunks),
            )
            .with_for_update()
        )
        return self._session.scalar(statement)

    def list_applications(self, job_id: UUID) -> list[JobApplication]:
        statement = (
            select(JobApplication)
            .where(JobApplication.job_id == job_id)
            .options(
                selectinload(JobApplication.candidate),
                selectinload(JobApplication.document).selectinload(CandidateDocument.profile),
            )
            .order_by(JobApplication.created_at, JobApplication.id)
        )
        return list(self._session.scalars(statement))

    def lock_for_application(self, application_id: UUID) -> CandidateScreening | None:
        statement = (
            select(CandidateScreening)
            .where(CandidateScreening.application_id == application_id)
            .options(*self._screening_options())
            .with_for_update()
        )
        return self._session.scalar(statement)

    def get_for_application(self, application_id: UUID) -> CandidateScreening | None:
        statement = (
            select(CandidateScreening)
            .where(CandidateScreening.application_id == application_id)
            .options(*self._screening_options())
        )
        return self._session.scalar(statement)

    def list_for_job(self, job_id: UUID) -> list[CandidateScreening]:
        statement = (
            select(CandidateScreening)
            .where(CandidateScreening.job_id == job_id)
            .options(*self._screening_options())
        )
        return list(self._session.scalars(statement).unique())

    def get_context(self, screening_id: UUID) -> CandidateScreeningContext | None:
        screening = self._session.scalar(
            select(CandidateScreening)
            .where(CandidateScreening.id == screening_id)
            .options(*self._screening_options())
        )
        if screening is None:
            return None
        job = self._session.scalar(
            select(Job).where(Job.id == screening.job_id).options(selectinload(Job.requirements))
        )
        application = self._session.scalar(
            select(JobApplication)
            .where(JobApplication.id == screening.application_id)
            .options(
                selectinload(JobApplication.candidate),
                selectinload(JobApplication.document).selectinload(CandidateDocument.profile),
                selectinload(JobApplication.document).selectinload(CandidateDocument.chunks),
            )
        )
        if (
            job is None
            or application is None
            or application.document is None
            or application.document.profile is None
        ):
            return None
        return CandidateScreeningContext(
            screening=screening,
            job=job,
            application=application,
            document=application.document,
            profile=application.document.profile,
        )

    def current_task(self, screening: CandidateScreening) -> ProcessingJob | None:
        if screening.processing_task_id is None:
            return None
        return self._session.get(ProcessingJob, screening.processing_task_id)

    def queue(self, screening: CandidateScreening, task: ProcessingJob) -> CandidateScreening:
        screening.status = CandidateScreeningStatus.QUEUED
        screening.processing_task_id = task.id
        screening.safe_error_code = None
        screening.safe_error_message = None
        self._session.add_all([screening, task])
        self._commit()
        return self.get_for_application(screening.application_id) or screening

    def begin(self, screening: CandidateScreening) -> None:
        screening.status = CandidateScreeningStatus.PROCESSING
        screening.started_at = datetime.now(UTC)
        screening.screened_at = None
        screening.safe_error_code = None
        screening.safe_error_message = None
        self._commit()

    def complete(
        self,
        screening: CandidateScreening,
        matches: list[CandidateRequirementMatch],
        *,
        required_met: int,
        required_partially_met: int,
        required_unmet: int,
        preferred_met: int,
        preferred_partially_met: int,
        preferred_unmet: int,
        provider: str,
        model: str,
    ) -> None:
        self._session.execute(
            delete(CandidateRequirementMatch).where(
                CandidateRequirementMatch.screening_id == screening.id
            )
        )
        self._session.flush()
        self._session.expire(screening, ["matches"])
        self._session.add_all(matches)
        screening.required_met = required_met
        screening.required_partially_met = required_partially_met
        screening.required_unmet = required_unmet
        screening.preferred_met = preferred_met
        screening.preferred_partially_met = preferred_partially_met
        screening.preferred_unmet = preferred_unmet
        screening.provider = provider
        screening.model = model
        screening.status = CandidateScreeningStatus.COMPLETED
        screening.safe_error_code = None
        screening.safe_error_message = None
        screening.screened_at = datetime.now(UTC)
        self._commit()

    def mark_failed(self, screening: CandidateScreening, *, code: str, message: str) -> None:
        screening.status = CandidateScreeningStatus.FAILED
        screening.safe_error_code = code
        screening.safe_error_message = message
        screening.screened_at = datetime.now(UTC)
        self._commit()

    @staticmethod
    def _screening_options() -> tuple[object, ...]:
        return (
            selectinload(CandidateScreening.matches)
            .selectinload(CandidateRequirementMatch.evidence)
            .selectinload(CandidateRequirementEvidence.chunk),
        )

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
