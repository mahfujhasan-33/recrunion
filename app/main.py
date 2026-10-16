import logging
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.auth import require_recruiter, safe_next_path
from app.config import get_settings
from app.errors import AuthenticationError, RecrUnionError
from app.routers import (
    applications_api,
    applications_web,
    assistant_api,
    assistant_web,
    auth_web,
    company_documents_api,
    company_documents_web,
    email_intake_api,
    health,
    home,
    interview_probes_api,
    interviews_api,
    interviews_web,
    job_publications_api,
    jobs_api,
    jobs_web,
    scoring_api,
    screening_api,
    tasks_api,
    transcripts_api,
)

STATIC_DIRECTORY = Path(__file__).parent / "static"


class CandidateJoinTokenRedactionFilter(logging.Filter):
    """Redact capability tokens from Uvicorn access-log request paths."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            path = record.args[2]
            if isinstance(path, str) and path.startswith("/interviews/join/"):
                args = list(record.args)
                args[2] = "/interviews/join/[REDACTED]"
                record.args = tuple(args)
        return True


def configure_logging() -> None:
    """Configure process logging from environment settings."""

    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    access_logger = logging.getLogger("uvicorn.access")
    if not any(
        isinstance(item, CandidateJoinTokenRedactionFilter) for item in access_logger.filters
    ):
        access_logger.addFilter(CandidateJoinTokenRedactionFilter())


def create_app() -> FastAPI:
    """Build the FastAPI application."""

    configure_logging()
    application = FastAPI(
        title="RecrUnion", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
    application.include_router(auth_web.router)
    application.include_router(health.router)
    # Candidate capabilities and service callbacks retain their own authentication.
    application.include_router(interviews_web.router)
    application.include_router(transcripts_api.router)
    for recruiter_router in (
        home.router,
        applications_web.router,
        email_intake_api.router,
        applications_api.router,
        interviews_api.router,
        interview_probes_api.router,
        screening_api.router,
        scoring_api.router,
        assistant_web.router,
        assistant_api.router,
        jobs_api.router,
        job_publications_api.router,
        jobs_web.router,
        company_documents_api.router,
        company_documents_web.router,
        tasks_api.router,
    ):
        application.include_router(recruiter_router, dependencies=[Depends(require_recruiter)])

    @application.get(
        "/openapi.json", include_in_schema=False, dependencies=[Depends(require_recruiter)]
    )
    def protected_openapi() -> JSONResponse:
        return JSONResponse(application.openapi())

    @application.get("/docs", include_in_schema=False, dependencies=[Depends(require_recruiter)])
    def protected_docs() -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/openapi.json", title="RecrUnion API")

    @application.middleware("http")
    async def private_response_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
            # Native login/logout forms need their same-origin Origin header.
            # Capability URLs still suppress all referrers, including same-origin ones.
            response.headers["Referrer-Policy"] = (
                "no-referrer" if request.url.path.startswith("/interviews/join/") else "same-origin"
            )
        return response

    @application.exception_handler(RecrUnionError)
    async def handle_application_error(
        request: Request,
        error: RecrUnionError,
    ) -> JSONResponse | RedirectResponse:
        if isinstance(error, AuthenticationError) and not request.url.path.startswith("/api/"):
            next_path = quote(safe_next_path(request.url.path), safe="")
            return RedirectResponse(f"/login?next={next_path}", status_code=303)
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        return JSONResponse(
            status_code=error.status_code,
            content={
                "code": error.code,
                "message": str(error),
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    return application


app = create_app()
