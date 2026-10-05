from typing import Any

from fastapi.testclient import TestClient


def test_job_pages_render_create_list_detail_and_edit(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    create_page = client.get("/jobs/new")
    assert create_page.status_code == 200
    assert "Create job" in create_page.text

    created = client.post("/api/v1/jobs", json=job_payload).json()

    list_page = client.get("/jobs")
    assert list_page.status_code == 200
    assert "Backend Engineer" in list_page.text

    detail_page = client.get(f"/jobs/{created['id']}")
    assert detail_page.status_code == 200
    assert created["code"] in detail_page.text
    assert "PostgreSQL" in detail_page.text

    edit_page = client.get(f"/jobs/{created['id']}/edit")
    assert edit_page.status_code == 200
    assert "Edit job" in edit_page.text
    assert "jobs@example.com" in edit_page.text
