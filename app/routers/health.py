from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.database import engine
from app.repositories.readiness import ReadinessRepository
from app.services.readiness import ReadinessService

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["ok", "unavailable"]
    pgvector: Literal["ok", "unavailable"]


def get_readiness_service() -> ReadinessService:
    """Provide the readiness service for request handling."""

    return ReadinessService(ReadinessRepository(engine))


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report that the application process is running."""

    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessResponse)
def ready(
    response: Response,
    readiness_service: Annotated[ReadinessService, Depends(get_readiness_service)],
) -> ReadinessResponse:
    """Report whether PostgreSQL and pgvector are available."""

    readiness = readiness_service.check()
    if not readiness.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if readiness.ready else "not_ready",
        database=readiness.database,
        pgvector=readiness.pgvector,
    )
