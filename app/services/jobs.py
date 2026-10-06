from decimal import Decimal
from uuid import UUID, uuid4

from app.errors import InvalidJobStatusError, JobNotFoundError
from app.models.jobs import (
    Job,
    JobRequirement,
    JobStatus,
    RequirementCategory,
    RequirementType,
)
from app.repositories.jobs import JobRepository
from app.schemas.jobs import JobResponse, JobWriteRequest


class JobService:
    """Implement job-management use cases."""

    def __init__(self, repository: JobRepository) -> None:
        self._repository = repository

    def create_job(self, request: JobWriteRequest) -> JobResponse:
        job_id = uuid4()
        job = Job(
            id=job_id,
            code=f"JOB-{job_id.hex[:8].upper()}",
            title=request.title,
            location=request.location,
            employment_type=request.employment_type,
            application_email=str(request.application_email),
            status=JobStatus.DRAFT,
            requirements=self._build_requirements(request),
        )
        return self.to_response(self._repository.create(job))

    def list_jobs(self) -> list[JobResponse]:
        return [self.to_response(job) for job in self._repository.list_all()]

    def get_job(self, job_id: UUID) -> JobResponse:
        return self.to_response(self._get_existing_job(job_id))

    def update_job(self, job_id: UUID, request: JobWriteRequest) -> JobResponse:
        job = self._get_existing_job(job_id)
        if job.status != JobStatus.DRAFT:
            raise InvalidJobStatusError(
                "Structured requirements can only be edited while the job is DRAFT."
            )
        job.title = request.title
        job.location = request.location
        job.employment_type = request.employment_type
        job.application_email = str(request.application_email)
        job.requirements = self._build_requirements(request)
        return self.to_response(self._repository.update(job))

    def _get_existing_job(self, job_id: UUID) -> Job:
        job = self._repository.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job

    @staticmethod
    def _build_requirements(request: JobWriteRequest) -> list[JobRequirement]:
        requirements: list[JobRequirement] = []
        priority = 1

        for text in request.required_skills:
            requirements.append(
                JobRequirement(
                    category=RequirementCategory.SKILL,
                    text=text,
                    requirement_type=RequirementType.REQUIRED,
                    priority=priority,
                )
            )
            priority += 1

        for text in request.preferred_skills:
            requirements.append(
                JobRequirement(
                    category=RequirementCategory.SKILL,
                    text=text,
                    requirement_type=RequirementType.PREFERRED,
                    priority=priority,
                )
            )
            priority += 1

        if request.minimum_experience is not None:
            requirements.append(
                JobRequirement(
                    category=RequirementCategory.EXPERIENCE,
                    text="Minimum relevant experience in years",
                    requirement_type=RequirementType.REQUIRED,
                    priority=priority,
                    minimum_value=Decimal(str(request.minimum_experience)),
                )
            )
            priority += 1

        for text in request.qualifications:
            requirements.append(
                JobRequirement(
                    category=RequirementCategory.QUALIFICATION,
                    text=text,
                    requirement_type=RequirementType.REQUIRED,
                    priority=priority,
                )
            )
            priority += 1

        for text in request.selection_criteria:
            requirements.append(
                JobRequirement(
                    category=RequirementCategory.SELECTION_CRITERION,
                    text=text,
                    requirement_type=RequirementType.REQUIRED,
                    priority=priority,
                )
            )
            priority += 1

        return requirements

    @staticmethod
    def to_response(job: Job) -> JobResponse:
        required_skills: list[str] = []
        preferred_skills: list[str] = []
        qualifications: list[str] = []
        selection_criteria: list[str] = []
        minimum_experience: float | None = None

        for requirement in job.requirements:
            if requirement.category == RequirementCategory.SKILL:
                target = (
                    required_skills
                    if requirement.requirement_type == RequirementType.REQUIRED
                    else preferred_skills
                )
                target.append(requirement.text)
            elif requirement.category == RequirementCategory.EXPERIENCE:
                if requirement.minimum_value is not None:
                    minimum_experience = float(requirement.minimum_value)
            elif requirement.category == RequirementCategory.QUALIFICATION:
                qualifications.append(requirement.text)
            elif requirement.category == RequirementCategory.SELECTION_CRITERION:
                selection_criteria.append(requirement.text)

        return JobResponse(
            id=job.id,
            code=job.code,
            title=job.title,
            location=job.location,
            employment_type=job.employment_type,
            application_email=job.application_email,
            status=job.status,
            jd_generated_content=job.jd_generated_content,
            jd_content=job.jd_content,
            jd_version=job.jd_version,
            jd_generated_at=job.jd_generated_at,
            approved_at=job.approved_at,
            jd_provider=job.jd_provider,
            jd_model=job.jd_model,
            jd_generation_metadata=job.jd_generation_metadata,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            minimum_experience=minimum_experience,
            qualifications=qualifications,
            selection_criteria=selection_criteria,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
