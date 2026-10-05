import logging
from dataclasses import dataclass

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DatabaseReadiness:
    """Availability of PostgreSQL and its pgvector extension."""

    database_available: bool
    pgvector_available: bool


class ReadinessRepository:
    """Read-only database checks used by the readiness service."""

    def __init__(self, database_engine: Engine) -> None:
        self._engine = database_engine

    def check(self) -> DatabaseReadiness:
        """Check database connectivity and required extension availability."""

        try:
            with self._engine.connect() as connection:
                pgvector_available = bool(
                    connection.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
                    )
                )
        except SQLAlchemyError:
            logger.warning(
                "Database readiness check failed",
                extra={"error_code": "DATABASE_UNAVAILABLE"},
            )
            return DatabaseReadiness(
                database_available=False,
                pgvector_available=False,
            )

        return DatabaseReadiness(
            database_available=True,
            pgvector_available=pgvector_available,
        )
