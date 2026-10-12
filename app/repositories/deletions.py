from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.applications import (
    ApplicationIntakeReceipt,
    Candidate,
    CandidateCVChunk,
    CandidateDocument,
    CandidateDocumentProcessingStatus,
    CandidateProfile,
    JobApplication,
)
from app.models.assistant import AssistantArtifact, AssistantConversation, AssistantMessage
from app.models.job_policy import JobDescriptionPolicyFinding, JobDescriptionPolicyReview
from app.models.job_publications import JobPublication, PublicationStatus
from app.models.jobs import Job, JobRequirement
from app.models.processing_jobs import ProcessingJob, ProcessingJobStatus, ProcessingJobType
from app.models.scoring import CandidateScore, JobScoringConfig
from app.models.screening import (
    CandidateRequirementEvidence,
    CandidateRequirementMatch,
    CandidateScreening,
    CandidateScreeningStatus,
)

ACTIVE_TASK_STATUSES = (ProcessingJobStatus.QUEUED, ProcessingJobStatus.RUNNING)
ACTIVE_DOCUMENT_STATUSES = (
    CandidateDocumentProcessingStatus.QUEUED,
    CandidateDocumentProcessingStatus.PROCESSING,
)
ACTIVE_SCREENING_STATUSES = (
    CandidateScreeningStatus.QUEUED,
    CandidateScreeningStatus.PROCESSING,
)
ACTIVE_PUBLICATION_STATUSES = (
    PublicationStatus.QUEUED,
    PublicationStatus.PUBLISHING,
)


@dataclass(frozen=True)
class ApplicationDeletionPlan:
    application_id: UUID
    candidate_id: UUID
    document_id: UUID
    storage_key: str
    task_ids: frozenset[UUID]
    is_busy: bool


@dataclass(frozen=True)
class JobDeletionPlan:
    job_id: UUID
    storage_keys: tuple[str, ...]
    candidate_ids: frozenset[UUID]
    task_ids: frozenset[UUID]
    is_busy: bool


class DeletionRepository:
    """Resolve and delete recruitment aggregates in one database transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def plan_application_deletion(
        self,
        job_id: UUID,
        application_id: UUID,
    ) -> ApplicationDeletionPlan | None:
        application = self._session.scalar(
            select(JobApplication).where(
                JobApplication.job_id == job_id,
                JobApplication.id == application_id,
            )
        )
        if application is None:
            return None
        document = self._session.scalar(
            select(CandidateDocument).where(CandidateDocument.application_id == application.id)
        )
        if document is None:
            return None

        screenings = list(
            self._session.scalars(
                select(CandidateScreening).where(
                    CandidateScreening.application_id == application.id
                )
            )
        )
        screening_ids = {item.id for item in screenings}
        task_ids = {
            task_id
            for task_id in (
                document.processing_task_id,
                *(item.processing_task_id for item in screenings),
            )
            if task_id is not None
        }
        entity_ids = {document.id, *screening_ids}
        related_tasks = list(
            self._session.scalars(
                select(ProcessingJob).where(
                    or_(
                        ProcessingJob.id.in_(task_ids),
                        ProcessingJob.entity_id.in_(entity_ids),
                        (
                            (ProcessingJob.job_type == ProcessingJobType.SYNC_EMAIL_APPLICATIONS)
                            & (ProcessingJob.entity_id == job_id)
                        ),
                    )
                )
            )
        )
        task_ids.update(item.id for item in related_tasks if item.entity_id in entity_ids)
        is_busy = (
            document.processing_status in ACTIVE_DOCUMENT_STATUSES
            or any(item.status in ACTIVE_SCREENING_STATUSES for item in screenings)
            or any(item.status in ACTIVE_TASK_STATUSES for item in related_tasks)
        )
        return ApplicationDeletionPlan(
            application_id=application.id,
            candidate_id=application.candidate_id,
            document_id=document.id,
            storage_key=document.storage_key,
            task_ids=frozenset(task_ids),
            is_busy=is_busy,
        )

    def plan_job_deletion(self, job_id: UUID) -> JobDeletionPlan | None:
        if self._session.get(Job, job_id) is None:
            return None

        applications = list(
            self._session.scalars(select(JobApplication).where(JobApplication.job_id == job_id))
        )
        candidate_ids = {item.candidate_id for item in applications}
        documents = list(
            self._session.scalars(
                select(CandidateDocument).where(CandidateDocument.job_id == job_id)
            )
        )
        document_ids = {item.id for item in documents}
        screenings = list(
            self._session.scalars(
                select(CandidateScreening).where(CandidateScreening.job_id == job_id)
            )
        )
        screening_ids = {item.id for item in screenings}
        publications = list(
            self._session.scalars(select(JobPublication).where(JobPublication.job_id == job_id))
        )
        receipts = list(
            self._session.scalars(
                select(ApplicationIntakeReceipt).where(ApplicationIntakeReceipt.job_id == job_id)
            )
        )

        task_ids = {
            task_id
            for task_id in (
                *(item.processing_task_id for item in documents),
                *(item.processing_task_id for item in screenings),
                *(item.processing_task_id for item in publications),
                *(item.processing_task_id for item in receipts),
            )
            if task_id is not None
        }
        entity_ids = {job_id, *document_ids, *screening_ids, *task_ids}

        conversation_ids = set(
            self._session.scalars(
                select(AssistantConversation.id).where(
                    AssistantConversation.active_job_id == job_id
                )
            )
        )
        assistant_message_ids: set[UUID] = set()
        if conversation_ids:
            assistant_message_ids.update(
                self._session.scalars(
                    select(AssistantMessage.id).where(
                        AssistantMessage.conversation_id.in_(conversation_ids)
                    )
                )
            )

        related_tasks = list(
            self._session.scalars(
                select(ProcessingJob).where(
                    or_(
                        ProcessingJob.id.in_(task_ids),
                        ProcessingJob.entity_id.in_(entity_ids),
                        ProcessingJob.entity_id.in_(assistant_message_ids),
                    )
                )
            )
        )
        task_ids.update(
            item.id
            for item in related_tasks
            if item.entity_id in entity_ids and item.job_type != ProcessingJobType.ASSISTANT_TURN
        )
        is_busy = (
            any(item.processing_status in ACTIVE_DOCUMENT_STATUSES for item in documents)
            or any(item.status in ACTIVE_SCREENING_STATUSES for item in screenings)
            or any(item.status in ACTIVE_PUBLICATION_STATUSES for item in publications)
            or any(item.status in ACTIVE_TASK_STATUSES for item in related_tasks)
        )
        return JobDeletionPlan(
            job_id=job_id,
            storage_keys=tuple(item.storage_key for item in documents),
            candidate_ids=frozenset(candidate_ids),
            task_ids=frozenset(task_ids),
            is_busy=is_busy,
        )

    def delete_application(self, plan: ApplicationDeletionPlan) -> None:
        screening_ids = set(
            self._session.scalars(
                select(CandidateScreening.id).where(
                    CandidateScreening.application_id == plan.application_id
                )
            )
        )
        self._delete_application_rows(
            application_ids={plan.application_id},
            document_ids={plan.document_id},
            screening_ids=screening_ids,
        )
        remaining_application = self._session.scalar(
            select(JobApplication.id).where(JobApplication.candidate_id == plan.candidate_id)
        )
        if remaining_application is None:
            self._execute(delete(Candidate).where(Candidate.id == plan.candidate_id))
        if plan.task_ids:
            self._execute(delete(ProcessingJob).where(ProcessingJob.id.in_(plan.task_ids)))
        self._commit()

    def delete_job(self, plan: JobDeletionPlan) -> None:
        application_ids = set(
            self._session.scalars(
                select(JobApplication.id).where(JobApplication.job_id == plan.job_id)
            )
        )
        document_ids = set(
            self._session.scalars(
                select(CandidateDocument.id).where(CandidateDocument.job_id == plan.job_id)
            )
        )
        screening_ids = set(
            self._session.scalars(
                select(CandidateScreening.id).where(CandidateScreening.job_id == plan.job_id)
            )
        )
        self._delete_application_rows(application_ids, document_ids, screening_ids)

        review_ids = set(
            self._session.scalars(
                select(JobDescriptionPolicyReview.id).where(
                    JobDescriptionPolicyReview.job_id == plan.job_id
                )
            )
        )
        if review_ids:
            self._execute(
                delete(JobDescriptionPolicyFinding).where(
                    JobDescriptionPolicyFinding.review_id.in_(review_ids)
                )
            )
        self._execute(
            delete(JobDescriptionPolicyReview).where(
                JobDescriptionPolicyReview.job_id == plan.job_id
            )
        )
        self._execute(delete(JobPublication).where(JobPublication.job_id == plan.job_id))
        self._execute(
            delete(ApplicationIntakeReceipt).where(ApplicationIntakeReceipt.job_id == plan.job_id)
        )
        self._execute(delete(JobScoringConfig).where(JobScoringConfig.job_id == plan.job_id))
        self._execute(delete(JobRequirement).where(JobRequirement.job_id == plan.job_id))
        self._execute(
            update(AssistantConversation)
            .where(AssistantConversation.active_job_id == plan.job_id)
            .values(active_job_id=None)
        )
        self._execute(
            update(AssistantArtifact)
            .where(AssistantArtifact.job_id == plan.job_id)
            .values(job_id=None)
        )
        self._execute(delete(Job).where(Job.id == plan.job_id))

        for candidate_id in plan.candidate_ids:
            remaining_application = self._session.scalar(
                select(JobApplication.id).where(JobApplication.candidate_id == candidate_id)
            )
            if remaining_application is None:
                self._execute(delete(Candidate).where(Candidate.id == candidate_id))
        if plan.task_ids:
            self._execute(delete(ProcessingJob).where(ProcessingJob.id.in_(plan.task_ids)))
        self._commit()

    def _delete_application_rows(
        self,
        application_ids: set[UUID],
        document_ids: set[UUID],
        screening_ids: set[UUID],
    ) -> None:
        if not application_ids:
            return
        match_ids: set[UUID] = set()
        if screening_ids:
            match_ids.update(
                self._session.scalars(
                    select(CandidateRequirementMatch.id).where(
                        CandidateRequirementMatch.screening_id.in_(screening_ids)
                    )
                )
            )
        if match_ids:
            self._execute(
                delete(CandidateRequirementEvidence).where(
                    CandidateRequirementEvidence.match_id.in_(match_ids)
                )
            )
        self._execute(
            delete(CandidateScore).where(CandidateScore.application_id.in_(application_ids))
        )
        if screening_ids:
            self._execute(
                delete(CandidateRequirementMatch).where(
                    CandidateRequirementMatch.screening_id.in_(screening_ids)
                )
            )
            self._execute(
                delete(CandidateScreening).where(CandidateScreening.id.in_(screening_ids))
            )
        self._execute(
            delete(CandidateProfile).where(CandidateProfile.application_id.in_(application_ids))
        )
        if document_ids:
            self._execute(
                delete(ApplicationIntakeReceipt).where(
                    ApplicationIntakeReceipt.candidate_document_id.in_(document_ids)
                )
            )
            self._execute(
                delete(CandidateCVChunk).where(CandidateCVChunk.document_id.in_(document_ids))
            )
            self._execute(delete(CandidateDocument).where(CandidateDocument.id.in_(document_ids)))
        self._execute(delete(JobApplication).where(JobApplication.id.in_(application_ids)))

    def _execute(self, statement: object) -> None:
        self._session.execute(statement.execution_options(synchronize_session=False))

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
