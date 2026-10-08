"""Persistence models exposed to SQLAlchemy metadata."""

from app.models.applications import (
    ApplicationSource,
    ApplicationStatus,
    Candidate,
    CandidateCVChunk,
    CandidateDocument,
    CandidateDocumentProcessingStatus,
    CandidateProfile,
    JobApplication,
)
from app.models.assistant import (
    AssistantArtifact,
    AssistantArtifactStatus,
    AssistantArtifactType,
    AssistantConversation,
    AssistantMessage,
    AssistantMessageRole,
)
from app.models.company_documents import (
    CompanyDocument,
    CompanyDocumentChunk,
    CompanyDocumentStatus,
    CompanyDocumentType,
)
from app.models.job_policy import (
    JobDescriptionPolicyFinding,
    JobDescriptionPolicyReview,
    JobDescriptionSection,
    PolicyAlignmentStatus,
)
from app.models.job_publications import JobPublication, PublicationStatus
from app.models.jobs import (
    EmploymentType,
    Job,
    JobRequirement,
    JobStatus,
    RequirementCategory,
    RequirementType,
)
from app.models.processing_jobs import ProcessingJob, ProcessingJobStatus, ProcessingJobType

__all__ = [
    "ApplicationSource",
    "ApplicationStatus",
    "AssistantArtifact",
    "AssistantArtifactStatus",
    "AssistantArtifactType",
    "AssistantConversation",
    "AssistantMessage",
    "AssistantMessageRole",
    "Candidate",
    "CandidateCVChunk",
    "CandidateDocument",
    "CandidateDocumentProcessingStatus",
    "CandidateProfile",
    "CompanyDocument",
    "CompanyDocumentChunk",
    "CompanyDocumentStatus",
    "CompanyDocumentType",
    "EmploymentType",
    "Job",
    "JobApplication",
    "JobDescriptionPolicyFinding",
    "JobDescriptionPolicyReview",
    "JobDescriptionSection",
    "JobPublication",
    "JobRequirement",
    "JobStatus",
    "PolicyAlignmentStatus",
    "ProcessingJob",
    "ProcessingJobStatus",
    "ProcessingJobType",
    "PublicationStatus",
    "RequirementCategory",
    "RequirementType",
]
