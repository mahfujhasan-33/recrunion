import logging
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.errors import JobNotFoundError
from app.routers import health, home, jobs_api, jobs_web

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
    application.include_router(health.router)
    application.include_router(jobs_api.router)
    application.include_router(jobs_web.router)

    @application.exception_handler(JobNotFoundError)
    async def handle_job_not_found(
        request: Request,
        error: JobNotFoundError,
    ) -> JSONResponse:
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "code": "JOB_NOT_FOUND",
                "message": str(error),
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    return application


app = create_app()
