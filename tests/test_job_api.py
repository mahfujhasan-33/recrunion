from copy import deepcopy
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient


def test_create_list_retrieve_and_update_job(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    create_response = client.post("/api/v1/jobs", json=job_payload)

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["status"] == "DRAFT"
    assert created["code"].startswith("JOB-")
    assert created["required_skills"] == ["Python", "PostgreSQL"]

    list_response = client.get("/api/v1/jobs")
    assert list_response.status_code == 200
    assert [job["id"] for job in list_response.json()] == [created["id"]]

    detail_response = client.get(f"/api/v1/jobs/{created['id']}")
    assert detail_response.status_code == 200
    assert detail_response.json() == created

    update_payload = deepcopy(job_payload)
    update_payload["title"] = "Senior Backend Engineer"
    update_payload["required_skills"] = ["Python", "SQLAlchemy"]
    update_payload["minimum_experience"] = 5
    update_response = client.put(
        f"/api/v1/jobs/{created['id']}",
        json=update_payload,
    )

    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["title"] == "Senior Backend Engineer"
    assert updated["required_skills"] == ["Python", "SQLAlchemy"]
    assert updated["minimum_experience"] == 5
    assert updated["status"] == "DRAFT"


def test_job_validation_rejects_invalid_input(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    invalid_payload = deepcopy(job_payload)
    invalid_payload["application_email"] = "not-an-email"
    invalid_payload["required_skills"] = []

    response = client.post("/api/v1/jobs", json=invalid_payload)

    assert response.status_code == 422
    error_locations = {tuple(error["loc"]) for error in response.json()["detail"]}
    assert ("body", "application_email") in error_locations
    assert ("body", "required_skills") in error_locations


def test_job_validation_rejects_duplicate_requirements(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    invalid_payload = deepcopy(job_payload)
    invalid_payload["required_skills"] = ["Python", "python"]

    response = client.post("/api/v1/jobs", json=invalid_payload)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "required_skills"]


def test_missing_job_returns_safe_not_found_error(client: TestClient) -> None:
    response = client.get(f"/api/v1/jobs/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "JOB_NOT_FOUND"
    assert response.json()["message"] == "Job was not found."
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
