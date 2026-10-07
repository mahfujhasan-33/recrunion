import logging
from collections.abc import Callable
from uuid import UUID, uuid4

from app.adapters.publisher import PublisherAdapter
from app.errors import (
    InvalidJobStatusError,
    JobNotFoundError,
    JobPublicationContentError,
    JobPublicationNotFoundError,
    PublisherError,
    PublisherUnavailableError,
)
from app.models.job_publications import JobPublication
from app.models.jobs import JobStatus
from app.models.processing_jobs import ProcessingJob, ProcessingJobType
from app.repositories.job_publications import JobPublicationRepository
from app.schemas.job_publications import JobPublicationResponse
from app.services.job_publication_content import JobPublicationContentBuilder
from app.services.jobs import JobService

ProgressReporter = Callable[[int, str], None]
logger = logging.getLogger(__name__)


class JobPublishingService:
    """Validate, queue, execute, and report approved-job publication attempts."""

    def __init__(
        self,
        repository: JobPublicationRepository,
        publisher: PublisherAdapter,
        content_builder: JobPublicationContentBuilder,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._content_builder = content_builder

    def publish(self, job_id: UUID) -> JobPublicationResponse:
        return self._queue(job_id, retry=False)

    def retry(self, job_id: UUID) -> JobPublicationResponse:
        return self._queue(job_id, retry=True)

    def get_latest(self, job_id: UUID) -> JobPublicationResponse | None:
        job = self._repository.get_job(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        publication = self._repository.latest_for_job(job_id)
        return self.to_response(publication) if publication else None

    async def process(self, publication_id: UUID, report: ProgressReporter) -> None:
        publication = self._repository.get(publication_id)
        if publication is None:
            raise JobPublicationNotFoundError
        self._repository.mark_publishing(publication)
        try:
            report(15, "Preparing the approved job announcement")
            content = self._content_builder.build(JobService.to_response(publication.job))
            report(35, "Authenticating with Bluesky")
            report(65, "Publishing the job announcement")
            result = await self._publisher.publish_job(content)
            self._repository.mark_published(publication, result)
            report(90, "Bluesky publication result saved")
        except (JobPublicationContentError, PublisherError) as error:
            self._repository.mark_failed(
                publication,
                code=error.code,
                message=str(error),
            )
            raise
        except Exception as error:
            safe_error = PublisherUnavailableError(
                "The publishing provider could not complete the request."
            )
            self._repository.mark_failed(
                publication,
                code=safe_error.code,
                message=str(safe_error),
            )
            logger.exception(
                "Unexpected publishing failure",
                extra={"publication_id": str(publication_id)},
            )
            raise safe_error from error

    def _queue(self, job_id: UUID, *, retry: bool) -> JobPublicationResponse:
        job = self._repository.lock_job(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        self._validate_transition(job.status, retry=retry)

        publication_id = uuid4()
        task_id = uuid4()
        publication = JobPublication(
            id=publication_id,
            job_id=job.id,
            processing_task_id=task_id,
            provider="BLUESKY",
            attempt_number=self._repository.next_attempt_number(job.id),
        )
        task = ProcessingJob(
            id=task_id,
            job_type=ProcessingJobType.JOB_PUBLICATION,
            entity_type="JOB_PUBLICATION",
            entity_id=publication_id,
            max_attempts=1,
            progress_message="Waiting to publish the approved job",
        )
        return self.to_response(self._repository.create_attempt(job, publication, task))

    @staticmethod
    def _validate_transition(status: JobStatus, *, retry: bool) -> None:
        if retry:
            if status != JobStatus.PUBLISH_FAILED:
                raise InvalidJobStatusError("Only a failed publication can be explicitly retried.")
            return
        if status == JobStatus.APPROVED:
            return
        messages = {
            JobStatus.DRAFT: "Generate, review, and approve the job description before publishing.",
            JobStatus.GENERATED: "Recruiter approval is required before publishing.",
            JobStatus.PUBLISHING: "This job is already being published.",
            JobStatus.PUBLISHED: "This job has already been published.",
            JobStatus.PUBLISH_FAILED: "Use the explicit retry action for a failed publication.",
            JobStatus.CLOSED: "A closed job cannot be published.",
        }
        raise InvalidJobStatusError(messages[status])

    @staticmethod
    def to_response(publication: JobPublication) -> JobPublicationResponse:
        return JobPublicationResponse.model_validate(publication, from_attributes=True)
