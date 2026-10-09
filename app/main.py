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
    health,
    home,
    job_publications_api,
    jobs_api,
    jobs_web,
    screening_api,
    tasks_api,
)

STATIC_DIRECTORY = Path(__file__).parent / "static"


def configure_logging() -> None:
    """Configure process logging from environment settings."""

    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app() -> FastAPI:
    """Build the FastAPI application."""

    configure_logging()
    application = FastAPI(title="RecrUnion", version="0.1.0")
    application.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
    application.include_router(home.router)
    application.include_router(applications_web.router)
    application.include_router(applications_api.router)
    application.include_router(screening_api.router)
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
