from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.dependencies import get_company_document_service
from app.models.company_documents import CompanyDocumentType
from app.services.company_documents import CompanyDocumentService

router = APIRouter(
    prefix="/company-documents",
    tags=["company document pages"],
    include_in_schema=False,
)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
CompanyDocumentServiceDependency = Annotated[
    CompanyDocumentService, Depends(get_company_document_service)
]


@router.get("", response_class=HTMLResponse)
def company_documents_page(
    request: Request,
    service: CompanyDocumentServiceDependency,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="company_documents/list.html",
        context={
            "documents": service.list_documents(),
            "document_types": list(CompanyDocumentType),
            "max_document_size_mb": get_settings().max_company_document_size_mb,
        },
    )
