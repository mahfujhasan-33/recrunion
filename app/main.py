import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.errors import RecrUnionError
from app.routers import (
    applications_api,
    applications_web,
    assistant_api,
    assistant_web,
    company_documents_api,
    company_documents_web,
    email_intake_api,
    health,
    home,
    interview_signaling,
    interviews_api,
    interviews_web,
    job_publications_api,
    jobs_api,
    jobs_web,
    scoring_api,
    screening_api,
    tasks_api,
)
from app.services.interview_signaling import SignalingHub

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
    application = FastAPI(title="RecrUnion", version="0.1.0")
    application.state.interview_signaling_hub = SignalingHub()
    application.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
    application.include_router(home.router)
    application.include_router(applications_web.router)
    application.include_router(interviews_web.router)
    application.include_router(interview_signaling.router)
    application.include_router(email_intake_api.router)
    application.include_router(applications_api.router)
    application.include_router(interviews_api.router)
    application.include_router(screening_api.router)
    application.include_router(scoring_api.router)
    application.include_router(assistant_web.router)
    application.include_router(assistant_api.router)
    application.include_router(health.router)
    application.include_router(jobs_api.router)
    application.include_router(job_publications_api.router)
    application.include_router(jobs_web.router)
    application.include_router(company_documents_api.router)
    application.include_router(company_documents_web.router)
    application.include_router(tasks_api.router)

    @application.exception_handler(RecrUnionError)
    async def handle_application_error(
        request: Request,
        error: RecrUnionError,
    ) -> JSONResponse:
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
