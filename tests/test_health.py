from fastapi.testclient import TestClient

from app.main import create_app
from app.routers.health import get_readiness_service
from app.services.readiness import ReadinessStatus


class StubReadinessService:
    def __init__(self, readiness: ReadinessStatus) -> None:
        self._readiness = readiness

    def check(self) -> ReadinessStatus:
        return self._readiness


def test_health_reports_running_application() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_database_and_pgvector_available() -> None:
    application = create_app()
    application.dependency_overrides[get_readiness_service] = lambda: StubReadinessService(
        ReadinessStatus(ready=True, database="ok", pgvector="ok")
    )

    with TestClient(application) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        "pgvector": "ok",
    }


def test_ready_returns_service_unavailable_when_database_is_down() -> None:
    application = create_app()
    application.dependency_overrides[get_readiness_service] = lambda: StubReadinessService(
        ReadinessStatus(
            ready=False,
            database="unavailable",
            pgvector="unavailable",
        )
    )

    with TestClient(application) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "database": "unavailable",
        "pgvector": "unavailable",
    }
