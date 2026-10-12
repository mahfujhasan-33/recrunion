from copy import deepcopy
from typing import Any
from uuid import uuid4

from conftest import FakeLLMAdapter
from fastapi.testclient import TestClient

from app.errors import LLMProviderError


def test_generate_edit_and_approve_job_description(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    created = client.post("/api/v1/jobs", json=job_payload).json()

    generation_response = client.post(f"/api/v1/jobs/{created['id']}/generate-description")
    assert generation_response.status_code == 200
    generated = generation_response.json()
    assert generated["status"] == "GENERATED"
    assert generated["jd_content"].startswith("# Backend Engineer")
    assert generated["jd_provider"] == "fake"

    reviewed_content = (
        generated["jd_content"]
        + "\n\nRecruiter review confirms ownership of production API quality."
    )
    edit_response = client.put(
        f"/api/v1/jobs/{created['id']}/description",
        json={"content": reviewed_content},
    )
    assert edit_response.status_code == 200
    assert edit_response.json()["jd_content"] == reviewed_content
    assert edit_response.json()["status"] == "GENERATED"

    stale_approval_response = client.post(f"/api/v1/jobs/{created['id']}/approve")
    assert stale_approval_response.status_code == 409
    assert stale_approval_response.json()["code"] == "POLICY_REVIEW_REQUIRED"

    review_response = client.post(f"/api/v1/jobs/{created['id']}/policy-review")
    assert review_response.status_code == 200
    assert review_response.json()["is_current"] is True

    approval_response = client.post(f"/api/v1/jobs/{created['id']}/approve")
    assert approval_response.status_code == 200
    assert approval_response.json()["status"] == "APPROVED"
    assert approval_response.json()["approved_at"] is not None


def test_description_endpoints_reject_missing_job_and_invalid_transition(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    missing_response = client.post(f"/api/v1/jobs/{uuid4()}/generate-description")
    assert missing_response.status_code == 404
    assert missing_response.json()["code"] == "JOB_NOT_FOUND"

    created = client.post("/api/v1/jobs", json=job_payload).json()
    approval_response = client.post(f"/api/v1/jobs/{created['id']}/approve")
    assert approval_response.status_code == 409
    assert approval_response.json()["code"] == "INVALID_JOB_STATUS"


def test_provider_failure_is_safe_and_leaves_job_unchanged(
    client: TestClient,
    job_payload: dict[str, Any],
    fake_llm_adapter: FakeLLMAdapter,
) -> None:
    created = client.post("/api/v1/jobs", json=job_payload).json()
    fake_llm_adapter.errors = [
        LLMProviderError("Internal provider detail."),
        LLMProviderError("Internal provider detail."),
    ]

    response = client.post(f"/api/v1/jobs/{created['id']}/generate-description")
    persisted = client.get(f"/api/v1/jobs/{created['id']}").json()

    assert response.status_code == 502
    assert response.json()["code"] == "LLM_PROVIDER_UNAVAILABLE"
    assert "traceback" not in response.text.casefold()
    assert persisted["status"] == "DRAFT"
    assert persisted["jd_content"] is None


def test_generated_job_rejects_structured_requirement_changes(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    created = client.post("/api/v1/jobs", json=job_payload).json()
    client.post(f"/api/v1/jobs/{created['id']}/generate-description")
    changed_payload = deepcopy(job_payload)
    changed_payload["required_skills"] = ["A newly added requirement"]

    response = client.put(f"/api/v1/jobs/{created['id']}", json=changed_payload)

    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_JOB_STATUS"


def test_enhancement_requires_relevant_ready_policy_evidence(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    created = client.post("/api/v1/jobs", json=job_payload).json()
    client.post(f"/api/v1/jobs/{created['id']}/generate-description")

    response = client.post(f"/api/v1/jobs/{created['id']}/enhance-description")

    assert response.status_code == 409
    assert response.json()["code"] == "POLICY_ENHANCEMENT_UNAVAILABLE"


def test_generated_job_page_always_offers_policy_recheck(
    client: TestClient,
    job_payload: dict[str, Any],
) -> None:
    created = client.post("/api/v1/jobs", json=job_payload).json()
    client.post(f"/api/v1/jobs/{created['id']}/generate-description")

    response = client.get(f"/jobs/{created['id']}")

    assert response.status_code == 200
    assert 'id="recheck-policy"' in response.text
    assert "/static/js/jobs.js?v=20261007-m3-publishing-1" in response.text
