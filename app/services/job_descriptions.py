from datetime import UTC, datetime
from uuid import UUID

from app.errors import (
    InvalidJobStatusError,
    JobDescriptionValidationError,
    JobNotFoundError,
)
from app.graphs.job_description import JobDescriptionGraph
from app.models.jobs import Job, JobStatus
from app.repositories.jobs import JobRepository
from app.schemas.job_descriptions import JobDescriptionUpdateRequest
from app.schemas.jobs import JobResponse
from app.services.jobs import JobService


class JobDescriptionService:
    """Implement AI generation, recruiter editing, and explicit approval."""

    def __init__(
        self,
        repository: JobRepository,
        graph: JobDescriptionGraph,
    ) -> None:
        self._repository = repository
        self._graph = graph

    async def generate_description(self, job_id: UUID) -> JobResponse:
        final_state = await self._graph.run(job_id)
        error = final_state.get("error")
        if error is not None:
            raise error
        return JobService.to_response(final_state["job"])

    def update_description(
        self,
        job_id: UUID,
        request: JobDescriptionUpdateRequest,
    ) -> JobResponse:
        job = self._get_existing_job(job_id)
        if job.status != JobStatus.GENERATED:
            raise InvalidJobStatusError(
                "A job description can only be edited while the job is GENERATED."
            )

        job.jd_content = request.content
        job.jd_version += 1
        return JobService.to_response(self._repository.update(job))

    def approve_description(self, job_id: UUID) -> JobResponse:
        job = self._get_existing_job(job_id)
        if job.status != JobStatus.GENERATED:
            raise InvalidJobStatusError("Only a GENERATED job description can be approved.")
        if job.jd_content is None or len(job.jd_content.strip()) < 50:
            raise JobDescriptionValidationError(
                "A reviewed job description is required before approval."
            )

        job.status = JobStatus.APPROVED
        job.approved_at = datetime.now(UTC)
        return JobService.to_response(self._repository.update(job))

    def _get_existing_job(self, job_id: UUID) -> Job:
        job = self._repository.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job
