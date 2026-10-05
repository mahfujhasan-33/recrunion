from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_job_service
from app.models.jobs import EmploymentType
from app.services.jobs import JobService

router = APIRouter(prefix="/jobs", tags=["job pages"], include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
JobServiceDependency = Annotated[JobService, Depends(get_job_service)]


@router.get("", response_class=HTMLResponse)
def job_list(request: Request, service: JobServiceDependency) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="jobs/list.html",
        context={"jobs": service.list_jobs()},
    )


@router.get("/new", response_class=HTMLResponse)
def create_job_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="jobs/form.html",
        context={"job": None, "employment_types": list(EmploymentType)},
    )


@router.get("/{job_id}", response_class=HTMLResponse)
def job_detail(
    request: Request,
    job_id: UUID,
    service: JobServiceDependency,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="jobs/detail.html",
        context={"job": service.get_job(job_id)},
    )


@router.get("/{job_id}/edit", response_class=HTMLResponse)
def edit_job_page(
    request: Request,
    job_id: UUID,
    service: JobServiceDependency,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="jobs/form.html",
        context={
            "job": service.get_job(job_id),
            "employment_types": list(EmploymentType),
        },
    )
