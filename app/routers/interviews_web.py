from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_interview_scheduling_service
from app.services.interview_scheduling import InterviewSchedulingService

router = APIRouter(tags=["candidate interview pages"], include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
InterviewSchedulingServiceDependency = Annotated[
    InterviewSchedulingService,
    Depends(get_interview_scheduling_service),
]


@router.get("/interviews/join/{token}", response_class=HTMLResponse)
def candidate_interview_page(
    request: Request,
    token: str,
    service: InterviewSchedulingServiceDependency,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="interviews/join.html",
        context={"interview": service.get_candidate_view(token)},
    )
