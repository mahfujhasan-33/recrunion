from fastapi.testclient import TestClient

from app.main import create_app


def test_application_starts_and_renders_home_page() -> None:
    application = create_app()

    with TestClient(application) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "RecrUnion is running" in response.text
