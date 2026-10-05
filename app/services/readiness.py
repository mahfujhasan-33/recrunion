from dataclasses import dataclass

from app.repositories.readiness import ReadinessRepository


@dataclass(frozen=True, slots=True)
class ReadinessStatus:
    """Application readiness state exposed to the HTTP layer."""

    ready: bool
    database: str
    pgvector: str


class ReadinessService:
    """Evaluate whether local application dependencies are ready."""

    def __init__(self, repository: ReadinessRepository) -> None:
        self._repository = repository

    def check(self) -> ReadinessStatus:
        """Return a safe readiness summary."""

        database = self._repository.check()
        return ReadinessStatus(
            ready=database.database_available and database.pgvector_available,
            database="ok" if database.database_available else "unavailable",
            pgvector="ok" if database.pgvector_available else "unavailable",
        )
