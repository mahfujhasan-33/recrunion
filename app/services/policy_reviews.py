import asyncio
import hashlib
import logging
from uuid import UUID

from app.adapters.embeddings import EmbeddingAdapter
from app.adapters.llm import LLMAdapter
from app.errors import (
    InvalidJobStatusError,
    JobDescriptionValidationError,
    JobNotFoundError,
    PolicyRetrievalError,
    RecrUnionError,
)
from app.graphs.job_description import (
    build_policy_retrieval_query,
    build_policy_review,
    requirements_from_job,
)
from app.models.job_policy import JobDescriptionPolicyReview
from app.models.jobs import JobStatus
from app.repositories.company_documents import CompanyDocumentRepository
from app.repositories.jobs import JobRepository
from app.repositories.policy_knowledge import PolicyKnowledgeRepository, PolicyReviewRepository
from app.schemas.job_descriptions import (
    LLMGenerationMetadata,
    PolicyAlignmentRequest,
    PolicyAlignmentResult,
)
from app.schemas.policy_findings import (
    PolicyAlignmentEvaluation,
    PolicyFindingResponse,
    PolicyReviewResponse,
)

logger = logging.getLogger(__name__)


class PolicyReviewService:
    def __init__(
        self,
        job_repository: JobRepository,
        policy_repository: PolicyKnowledgeRepository,
        review_repository: PolicyReviewRepository,
        document_repository: CompanyDocumentRepository,
        embedding_adapter: EmbeddingAdapter,
        llm_adapter: LLMAdapter,
        *,
        candidate_count: int,
        top_k: int,
        minimum_similarity: float,
    ) -> None:
        self._job_repository = job_repository
        self._policy_repository = policy_repository
        self._review_repository = review_repository
        self._document_repository = document_repository
        self._embedding_adapter = embedding_adapter
        self._llm_adapter = llm_adapter
        self._candidate_count = candidate_count
        self._top_k = top_k
        self._minimum_similarity = minimum_similarity

    async def recheck(self, job_id: UUID) -> PolicyReviewResponse:
        job = self._job_repository.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        if job.status != JobStatus.GENERATED or not job.jd_content:
            raise InvalidJobStatusError(
                "Policy alignment can only be checked for a GENERATED job description."
            )
        requirements = requirements_from_job(job)
        evidence = []
        try:
            if self._policy_repository.has_ready_documents(self._embedding_adapter.model_name):
                query_embedding = await asyncio.to_thread(
                    self._embedding_adapter.embed_query,
                    build_policy_retrieval_query(requirements),
                )
                evidence = self._policy_repository.retrieve(
                    query_embedding,
                    embedding_model=self._embedding_adapter.model_name,
                    candidate_count=self._candidate_count,
                    top_k=self._top_k,
                    minimum_similarity=self._minimum_similarity,
                )
        except RecrUnionError:
            raise
        except Exception as error:
            logger.exception(
                "Company-policy retrieval failed during review",
                extra={"job_id": str(job_id), "operation": "policy_recheck"},
            )
            raise PolicyRetrievalError("Company-policy retrieval failed.") from error
        requirements = requirements.model_copy(update={"policy_evidence": evidence})
        if evidence:
            result = await self._llm_adapter.evaluate_policy_alignment(
                PolicyAlignmentRequest(
                    job_id=job.id,
                    content=job.jd_content,
                    requirements=requirements,
                    evidence=evidence,
                )
            )
        else:
            result = PolicyAlignmentResult(
                evaluation=PolicyAlignmentEvaluation(findings=[]),
                provider=job.jd_provider or "none",
                model=job.jd_model or "none",
                metadata=LLMGenerationMetadata(),
            )
        expected_ids = {item.evidence_id for item in evidence}
        finding_ids = [item.evidence_id for item in result.evaluation.findings]
        if len(finding_ids) != len(expected_ids) or set(finding_ids) != expected_ids:
            raise JobDescriptionValidationError(
                "The policy-alignment result did not pass evidence validation."
            )
        review = self._review_repository.save(
            build_policy_review(
                job,
                job.jd_content,
                evidence,
                result,
                self._embedding_adapter.model_name,
            )
        )
        return self._to_response(job_id, job.jd_version, job.jd_content, review)

    def get_latest(self, job_id: UUID) -> PolicyReviewResponse:
        job = self._job_repository.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        review = self._review_repository.latest(job_id)
        if review is None:
            return PolicyReviewResponse(
                id=None,
                job_id=job.id,
                jd_version=job.jd_version,
                is_current=False,
                retrieval_count=0,
                actionable_finding_count=0,
                created_at=None,
                findings=[],
                message="Policy alignment has not been checked.",
            )
        return self._to_response(job.id, job.jd_version, job.jd_content or "", review)

    def _to_response(
        self,
        job_id: UUID,
        jd_version: int,
        content: str,
        review: JobDescriptionPolicyReview,
    ) -> PolicyReviewResponse:
        current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        document_ids = {finding.source_document_id for finding in review.findings}
        available_ids = self._document_repository.existing_ids(document_ids)
        actionable_statuses = {"PARTIALLY_MET", "NOT_MET"}
        return PolicyReviewResponse(
            id=review.id,
            job_id=job_id,
            jd_version=review.jd_version,
            is_current=(
                review.jd_version == jd_version and review.jd_content_sha256 == current_hash
            ),
            retrieval_count=review.retrieval_count,
            actionable_finding_count=sum(
                finding.status.value in actionable_statuses for finding in review.findings
            ),
            created_at=review.created_at,
            findings=[
                PolicyFindingResponse(
                    id=finding.id,
                    source_document_id=finding.source_document_id,
                    source_chunk_id=finding.source_chunk_id,
                    source_filename=finding.source_filename,
                    source_document_type=finding.source_document_type,
                    source_available=finding.source_document_id in available_ids,
                    evidence_excerpt=finding.evidence_excerpt,
                    page_number=finding.page_number,
                    section_title=finding.section_title,
                    related_jd_section=finding.related_jd_section,
                    status=finding.status,
                    explanation=finding.explanation,
                )
                for finding in review.findings
            ],
            message=(
                None
                if review.retrieval_count
                else "No relevant ready company-policy evidence was found."
            ),
        )
