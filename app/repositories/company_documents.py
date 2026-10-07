from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.company_documents import CompanyDocument


class CompanyDocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, document: CompanyDocument) -> CompanyDocument:
        self._session.add(document)
        self._commit()
        return document

    def get(self, document_id: UUID) -> CompanyDocument | None:
        return self._session.get(CompanyDocument, document_id)

    def find_by_sha256(self, sha256: str) -> CompanyDocument | None:
        return self._session.scalar(select(CompanyDocument).where(CompanyDocument.sha256 == sha256))

    def list_all(self) -> list[CompanyDocument]:
        return list(
            self._session.scalars(
                select(CompanyDocument).order_by(CompanyDocument.uploaded_at.desc())
            )
        )

    def update(self, document: CompanyDocument) -> CompanyDocument:
        self._commit()
        return document

    def delete(self, document: CompanyDocument) -> None:
        self._session.delete(document)
        self._commit()

    def existing_ids(self, document_ids: set[UUID]) -> set[UUID]:
        if not document_ids:
            return set()
        return set(
            self._session.scalars(
                select(CompanyDocument.id).where(CompanyDocument.id.in_(document_ids))
            )
        )

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
