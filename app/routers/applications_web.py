from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.dependencies import get_application_intake_service, get_job_service
from app.services.application_intake import ApplicationIntakeService
from app.services.jobs import JobService

router = APIRouter(prefix="/jobs", tags=["application pages"], include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
ApplicationIntakeServiceDependency = Annotated[
    ApplicationIntakeService,
    Depends(get_application_intake_service),
]
JobServiceDependency = Annotated[JobService, Depends(get_job_service)]


@router.get("/{job_id}/applications", response_class=HTMLResponse)
def applications_page(
    request: Request,
    job_id: UUID,
    application_service: ApplicationIntakeServiceDependency,
    job_service: JobServiceDependency,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="applications/list.html",
        context={
            "job": job_service.get_job(job_id),
            "applications": application_service.list_applications(job_id),
            "max_cv_size_mb": get_settings().max_cv_size_mb,
        },
    )
