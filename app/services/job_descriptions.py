import hashlib
from datetime import UTC, datetime
from uuid import UUID

from app.errors import (
    InvalidJobStatusError,
    JobDescriptionValidationError,
    JobNotFoundError,
    PolicyReviewRequiredError,
)
from app.graphs.job_description import JobDescriptionGraph
from app.graphs.job_description_enhancement import JobDescriptionEnhancementGraph
from app.models.jobs import Job, JobStatus
from app.repositories.jobs import JobRepository
from app.repositories.policy_knowledge import PolicyReviewRepository
from app.schemas.job_descriptions import JobDescriptionUpdateRequest
from app.schemas.jobs import JobResponse
from app.services.jobs import JobService


class JobDescriptionService:
    """Implement AI generation, recruiter editing, and explicit approval."""

    def __init__(
        self,
        repository: JobRepository,
        graph: JobDescriptionGraph,
        enhancement_graph: JobDescriptionEnhancementGraph,
        policy_review_repository: PolicyReviewRepository,
    ) -> None:
        self._repository = repository
        self._graph = graph
        self._enhancement_graph = enhancement_graph
        self._policy_review_repository = policy_review_repository

    async def generate_description(self, job_id: UUID) -> JobResponse:
        final_state = await self._graph.run(job_id)
        error = final_state.get("error")
        if error is not None:
            raise error
        return JobService.to_response(final_state["job"])

    async def enhance_description(self, job_id: UUID) -> JobResponse:
        final_state = await self._enhancement_graph.run(job_id)
        error = final_state.get("error")
        if error is not None:
            raise error
        return JobService.to_response(final_state["job"])

    async def preview_enhancement(self, job_id: UUID) -> tuple[str, int, list[dict[str, object]]]:
        final_state = await self._enhancement_graph.run(job_id, persist=False)
        error = final_state.get("error")
        if error is not None:
            raise error
        findings = [
            finding.model_dump(mode="json")
            for finding in final_state["enhanced_policy_result"].evaluation.findings
        ]
        return final_state["enhanced_content"], final_state["job"].jd_version, findings

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

        review = self._policy_review_repository.latest_for_version(job.id, job.jd_version)
        content_hash = hashlib.sha256(job.jd_content.encode("utf-8")).hexdigest()
        if review is None or review.jd_content_sha256 != content_hash:
            raise PolicyReviewRequiredError(
                "Run policy alignment for the current job-description version before approval."
            )

        job.status = JobStatus.APPROVED
        job.approved_at = datetime.now(UTC)
        return JobService.to_response(self._repository.update(job))

    def _get_existing_job(self, job_id: UUID) -> Job:
        job = self._repository.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job
