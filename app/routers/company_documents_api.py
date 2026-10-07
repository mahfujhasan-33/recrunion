from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status

from app.dependencies import get_company_document_service
from app.errors import CompanyDocumentValidationError
from app.models.company_documents import CompanyDocumentType
from app.schemas.company_documents import CompanyDocumentResponse, CompanyDocumentUploadResponse
from app.services.company_documents import CompanyDocumentService

router = APIRouter(prefix="/api/v1/company-documents", tags=["company documents"])
CompanyDocumentServiceDependency = Annotated[
    CompanyDocumentService, Depends(get_company_document_service)
]


@router.post("", response_model=CompanyDocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED)
def upload_company_document(
    service: CompanyDocumentServiceDependency,
    document_type: Annotated[CompanyDocumentType, Form()],
    file: Annotated[UploadFile, File()],
) -> CompanyDocumentUploadResponse:
    if not file.filename or not file.content_type:
        raise CompanyDocumentValidationError("Document filename and content type are required.")
    return service.upload(
        filename=file.filename,
        content_type=file.content_type,
        document_type=document_type,
        source=file.file,
    )


@router.get("", response_model=list[CompanyDocumentResponse])
def list_company_documents(
    service: CompanyDocumentServiceDependency,
) -> list[CompanyDocumentResponse]:
    return service.list_documents()


@router.get("/{document_id}", response_model=CompanyDocumentResponse)
def get_company_document(
    document_id: UUID,
    service: CompanyDocumentServiceDependency,
) -> CompanyDocumentResponse:
    return service.get_document(document_id)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company_document(
    document_id: UUID,
    service: CompanyDocumentServiceDependency,
) -> Response:
    service.delete_document(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
