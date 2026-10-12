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
from app.models.job_publications import JobPublication, PublicationStatus
from app.models.jobs import Job, JobStatus
from app.models.processing_jobs import ProcessingJob, ProcessingJobType
from app.repositories.job_publications import JobPublicationRepository
from app.schemas.job_publications import (
    JobPublicationBatchResponse,
    JobPublicationResponse,
    PublicationProvider,
)
from app.services.job_publication_content import JobPublicationContentBuilder
from app.services.jobs import JobService

ProgressReporter = Callable[[int, str], None]
logger = logging.getLogger(__name__)


class JobPublishingService:
    """Queue and execute provider-independent publication attempts."""

    def __init__(
        self,
        repository: JobPublicationRepository,
        publishers: dict[PublicationProvider, PublisherAdapter],
        content_builder: JobPublicationContentBuilder,
    ) -> None:
        self._repository = repository
        self._publishers = publishers
        self._content_builder = content_builder

    def publish(
        self,
        job_id: UUID,
        providers: list[PublicationProvider] | None = None,
    ) -> JobPublicationBatchResponse:
        selected = providers or [PublicationProvider.BLUESKY]
        return self._queue(job_id, selected, retry=False)

    def retry(
        self,
        job_id: UUID,
        provider: PublicationProvider | None = None,
    ) -> JobPublicationBatchResponse:
        if provider is None:
            latest_by_provider: dict[str, JobPublication] = {}
            for publication in self._repository.list_for_job(job_id):
                latest_by_provider.setdefault(publication.provider, publication)
            failed = [
                item
                for item in latest_by_provider.values()
                if item.status == PublicationStatus.FAILED
            ]
            if not failed:
                raise JobPublicationNotFoundError
            if len(failed) > 1:
                raise InvalidJobStatusError("Choose the failed publication provider to retry.")
            provider = PublicationProvider(failed[0].provider)
        return self._queue(job_id, [provider], retry=True)

    def get_latest(self, job_id: UUID) -> JobPublicationResponse | None:
        job = self._repository.get_job(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        publication = self._repository.latest_for_job(job_id)
        return self.to_response(publication) if publication else None

    def list_latest(self, job_id: UUID) -> list[JobPublicationResponse]:
        job = self._repository.get_job(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        latest: dict[str, JobPublication] = {}
        for publication in self._repository.list_for_job(job_id):
            latest.setdefault(publication.provider, publication)
        return [self.to_response(item) for item in latest.values()]

    async def process(self, task_id: UUID, report: ProgressReporter) -> None:
        publications = self._repository.list_for_task(task_id)
        if not publications:
            legacy_publication = self._repository.get(task_id)
            if legacy_publication is not None:
                publications = self._repository.list_for_task(legacy_publication.processing_task_id)
        if not publications:
            raise JobPublicationNotFoundError
        ordered = sorted(
            publications,
            key=lambda item: 0 if item.provider == PublicationProvider.BLOGGER.value else 1,
        )
        blogger_url: str | None = self._existing_blogger_url(ordered[0].job_id)
        failures: list[PublisherError] = []
        for index, publication in enumerate(ordered):
            self._repository.mark_publishing(publication)
            base_progress = 10 + index * max(1, 70 // len(ordered))
            report(base_progress, f"Preparing {publication.provider.title()} publication")
            try:
                provider = PublicationProvider(publication.provider)
                publisher = self._publishers.get(provider)
                if publisher is None:
                    raise PublisherUnavailableError(
                        f"{publication.provider.title()} publishing is not configured."
                    )
                content = self._content_builder.build(
                    JobService.to_response(publication.job),
                    full_post_url=(
                        blogger_url if provider == PublicationProvider.BLUESKY else None
                    ),
                )
                report(base_progress + 15, f"Publishing to {publication.provider.title()}")
                result = await publisher.publish_job(content)
                self._repository.mark_published(publication, result)
                if provider == PublicationProvider.BLOGGER:
                    blogger_url = result.external_url
            except (JobPublicationContentError, PublisherError) as error:
                self._repository.mark_failed(
                    publication,
                    code=error.code,
                    message=str(error),
                )
                if isinstance(error, PublisherError):
                    failures.append(error)
            except Exception:
                safe_error = PublisherUnavailableError(
                    f"{publication.provider.title()} could not complete the request."
                )
                self._repository.mark_failed(
                    publication,
                    code=safe_error.code,
                    message=str(safe_error),
                )
                logger.exception(
                    "Unexpected publishing failure",
                    extra={"publication_id": str(publication.id)},
                )
        if len(ordered) == 1:
            report(90, f"{ordered[0].provider.title()} publication result saved")
        else:
            report(92, "Saving independent publication results")
        self._repository.finalize_job_status(ordered[0].job)
        if failures and len(failures) == len(ordered):
            raise failures[0]

    def _queue(
        self,
        job_id: UUID,
        providers: list[PublicationProvider],
        *,
        retry: bool,
    ) -> JobPublicationBatchResponse:
        job = self._repository.lock_job(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        self._validate_job(job)
        if len(providers) != len(set(providers)):
            raise InvalidJobStatusError("Publication providers must be unique.")

        for provider in providers:
            latest = self._repository.latest_for_provider(job.id, provider.value)
            if retry:
                if latest is None or latest.status != PublicationStatus.FAILED:
                    raise InvalidJobStatusError(
                        f"Only a failed {provider.value.title()} publication can be retried."
                    )
            elif latest is not None:
                if latest.status == PublicationStatus.PUBLISHED:
                    raise InvalidJobStatusError(
                        f"This job is already published on {provider.value.title()}."
                    )
                if latest.status in {PublicationStatus.QUEUED, PublicationStatus.PUBLISHING}:
                    raise InvalidJobStatusError(
                        f"This job is already being published on {provider.value.title()}."
                    )
                raise InvalidJobStatusError(
                    f"Use the explicit retry action for the failed {provider.value.title()} "
                    "publication."
                )

        task_id = uuid4()
        attempts = [
            JobPublication(
                id=uuid4(),
                job_id=job.id,
                processing_task_id=task_id,
                provider=provider.value,
                attempt_number=self._repository.next_attempt_number(job.id, provider.value),
                published_jd_version=job.jd_version,
            )
            for provider in providers
        ]
        task = ProcessingJob(
            id=task_id,
            job_type=ProcessingJobType.JOB_PUBLICATION,
            entity_type="JOB_PUBLICATION_BATCH",
            entity_id=task_id,
            max_attempts=1,
            progress_message="Waiting to publish the approved job",
        )
        created = self._repository.create_attempts(job, attempts, task)
        return JobPublicationBatchResponse(
            processing_task_id=task_id,
            publications=[self.to_response(item) for item in created],
        )

    @staticmethod
    def _validate_job(job: Job) -> None:
        if job.approved_at is None or not job.jd_content:
            messages = {
                JobStatus.DRAFT: (
                    "Generate, review, and approve the job description before publishing."
                ),
                JobStatus.GENERATED: "Recruiter approval is required before publishing.",
                JobStatus.CLOSED: "A closed job cannot be published.",
            }
            raise InvalidJobStatusError(
                messages.get(job.status, "The job must be approved before publishing.")
            )
        if job.status == JobStatus.CLOSED:
            raise InvalidJobStatusError("A closed job cannot be published.")

    def _existing_blogger_url(self, job_id: UUID) -> str | None:
        latest = self._repository.latest_for_provider(job_id, PublicationProvider.BLOGGER.value)
        if latest and latest.status == PublicationStatus.PUBLISHED:
            return latest.external_url
        return None

    @staticmethod
    def to_response(publication: JobPublication) -> JobPublicationResponse:
        return JobPublicationResponse.model_validate(publication, from_attributes=True)
