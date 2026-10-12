from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.document_parser import CompanyDocumentParser
from app.adapters.document_storage import LocalDocumentStorage
from app.dependencies import get_company_document_service
from app.errors import (
    CompanyDocumentDuplicateError,
    CompanyDocumentTooLargeError,
    CompanyDocumentValidationError,
)
from app.models.company_documents import CompanyDocumentType
from app.repositories.company_documents import CompanyDocumentRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.services.company_documents import CompanyDocumentService


def build_service(db_session: Session, root: Path) -> CompanyDocumentService:
    return CompanyDocumentService(
        CompanyDocumentRepository(db_session),
        ProcessingJobRepository(db_session),
        LocalDocumentStorage(root, 1024 * 1024),
        CompanyDocumentParser(),
        worker_max_attempts=3,
    )


def test_upload_list_duplicate_and_delete_company_document(
    db_session: Session,
    tmp_path: Path,
) -> None:
    service = build_service(db_session, tmp_path)

    uploaded = service.upload(
        filename="hiring-policy.txt",
        content_type="text/plain",
        document_type=CompanyDocumentType.HIRING_POLICY,
        source=BytesIO(b"All job descriptions must state the application process."),
    )

    assert uploaded.document.status.value == "UPLOADED"
    assert service.list_documents()[0].original_filename == "hiring-policy.txt"
    with pytest.raises(CompanyDocumentDuplicateError):
        service.upload(
            filename="copy.txt",
            content_type="text/plain",
            document_type=CompanyDocumentType.OTHER,
            source=BytesIO(b"All job descriptions must state the application process."),
        )

    service.delete_document(uploaded.document.id)
    assert service.list_documents() == []
    assert list(tmp_path.iterdir()) == []


def test_company_document_api_and_page(
    application: FastAPI,
    client: TestClient,
    db_session: Session,
    tmp_path: Path,
) -> None:
    service = build_service(db_session, tmp_path)
    application.dependency_overrides[get_company_document_service] = lambda: service

    response = client.post(
        "/api/v1/company-documents",
        data={"document_type": "JOB_DESCRIPTION_STANDARD"},
        files={"file": ("standards.txt", b"Use inclusive language.", "text/plain")},
    )

    assert response.status_code == 202
    assert response.json()["document"]["status"] == "UPLOADED"
    assert client.get("/api/v1/company-documents").status_code == 200
    page = client.get("/company-documents")
    assert page.status_code == 200
    assert "standards.txt" in page.text


def test_upload_rejects_invalid_type_and_oversized_file(
    db_session: Session,
    tmp_path: Path,
) -> None:
    service = build_service(db_session, tmp_path)

    with pytest.raises(CompanyDocumentValidationError):
        service.upload(
            filename="policy.csv",
            content_type="text/csv",
            document_type=CompanyDocumentType.OTHER,
            source=BytesIO(b"policy,data"),
        )

    with pytest.raises(CompanyDocumentTooLargeError):
        service.upload(
            filename="policy.txt",
            content_type="text/plain",
            document_type=CompanyDocumentType.OTHER,
            source=BytesIO(b"x" * (1024 * 1024 + 1)),
        )

    assert list(tmp_path.iterdir()) == []
