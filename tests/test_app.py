from fastapi.testclient import TestClient


def test_application_starts_and_renders_home_page(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "RecrUnion is running" in response.text
