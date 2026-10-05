import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import health, home

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
    return application


app = create_app()
