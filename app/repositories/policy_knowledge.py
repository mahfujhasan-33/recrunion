from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models.company_documents import (
    CompanyDocument,
    CompanyDocumentChunk,
    CompanyDocumentStatus,
)
from app.models.job_policy import JobDescriptionPolicyReview
from app.schemas.policy_findings import PolicyEvidence


class PolicyKnowledgeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def retrieve(
        self,
        query_embedding: list[float],
        *,
        embedding_model: str,
        candidate_count: int,
        top_k: int,
        minimum_similarity: float,
    ) -> list[PolicyEvidence]:
        distance = CompanyDocumentChunk.embedding.cosine_distance(query_embedding).label("distance")
        statement = (
            select(CompanyDocumentChunk, CompanyDocument, distance)
            .join(CompanyDocument, CompanyDocument.id == CompanyDocumentChunk.document_id)
            .where(
                CompanyDocument.status == CompanyDocumentStatus.READY,
                CompanyDocument.embedding_model == embedding_model,
            )
            .order_by(distance)
            .limit(candidate_count)
        )
        candidates = self._session.execute(statement).all()
        evidence: list[PolicyEvidence] = []
        per_document: dict[UUID, int] = {}
        for chunk, document, distance_value in candidates:
            similarity = 1.0 - float(distance_value)
            if similarity < minimum_similarity or per_document.get(document.id, 0) >= 2:
                continue
            evidence.append(
                PolicyEvidence(
                    evidence_id=chunk.id,
                    document_id=document.id,
                    document_filename=document.original_filename,
                    document_type=document.document_type.value,
                    document_checksum=document.sha256,
                    content=chunk.content,
                    page_number=chunk.page_number,
                    section_title=chunk.section_title,
                    similarity=similarity,
                )
            )
            per_document[document.id] = per_document.get(document.id, 0) + 1
            if len(evidence) == top_k:
                break
        return evidence

    def has_ready_documents(self, embedding_model: str) -> bool:
        statement = (
            select(CompanyDocument.id)
            .where(
                CompanyDocument.status == CompanyDocumentStatus.READY,
                CompanyDocument.embedding_model == embedding_model,
            )
            .limit(1)
        )
        return self._session.scalar(statement) is not None


class PolicyReviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, review: JobDescriptionPolicyReview) -> JobDescriptionPolicyReview:
        self._session.add(review)
        self._commit()
        return review

    def latest(self, job_id: UUID) -> JobDescriptionPolicyReview | None:
        statement = (
            select(JobDescriptionPolicyReview)
            .where(JobDescriptionPolicyReview.job_id == job_id)
            .options(selectinload(JobDescriptionPolicyReview.findings))
            .order_by(
                JobDescriptionPolicyReview.jd_version.desc(),
                JobDescriptionPolicyReview.created_at.desc(),
            )
            .limit(1)
        )
        return self._session.scalar(statement)

    def latest_for_version(
        self, job_id: UUID, jd_version: int
    ) -> JobDescriptionPolicyReview | None:
        statement = (
            select(JobDescriptionPolicyReview)
            .where(
                JobDescriptionPolicyReview.job_id == job_id,
                JobDescriptionPolicyReview.jd_version == jd_version,
            )
            .options(selectinload(JobDescriptionPolicyReview.findings))
            .order_by(JobDescriptionPolicyReview.created_at.desc())
            .limit(1)
        )
        return self._session.scalar(statement)

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
