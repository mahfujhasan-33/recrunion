from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import get_interview_room_service
from app.schemas.interviews import InterviewLifecycleResponse
from app.services.interview_rooms import InterviewRoomService

router = APIRouter(tags=["interview room pages"], include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
InterviewRoomServiceDependency = Annotated[
    InterviewRoomService,
    Depends(get_interview_room_service),
]


@router.get("/interviews/join/{token}", response_class=HTMLResponse)
def candidate_interview_page(
    request: Request,
    token: str,
    service: InterviewRoomServiceDependency,
) -> HTMLResponse:
    room = service.get_candidate_room(token)
    return templates.TemplateResponse(
        request=request,
        name="interviews/join.html",
        context={"room": room, "room_config": room.model_dump(mode="json")},
    )


@router.post(
    "/interviews/join/{token}/lifecycle-sync",
    response_model=InterviewLifecycleResponse,
)
async def sync_candidate_interview_lifecycle(
    token: str,
    service: InterviewRoomServiceDependency,
) -> InterviewLifecycleResponse:
    interview = await service.sync_candidate_lifecycle(token)
    return InterviewLifecycleResponse(
        interview_id=interview.id,
        status=interview.status,
        started_at=interview.started_at,
        ended_at=interview.ended_at,
    )


@router.get("/interviews/{interview_id}/room", response_class=HTMLResponse)
def recruiter_interview_page(
    request: Request,
    interview_id: UUID,
    service: InterviewRoomServiceDependency,
) -> HTMLResponse:
    room = service.get_interviewer_room(interview_id)
    return templates.TemplateResponse(
        request=request,
        name="interviews/join.html",
        context={"room": room, "room_config": room.model_dump(mode="json")},
    )
