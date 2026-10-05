from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(include_in_schema=False)

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Render the M0 application landing page."""

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={},
    )
