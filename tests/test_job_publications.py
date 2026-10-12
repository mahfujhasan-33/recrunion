from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.bluesky import BlueskyPublisher
from app.adapters.publisher import JobPublicationContent
from app.dependencies import build_job_publishing_service
from app.errors import (
    InvalidJobStatusError,
    PublisherAuthenticationError,
    PublisherConfigurationError,
    PublisherInvalidResponseError,
    PublisherUnavailableError,
)
from app.models.job_publications import PublicationStatus
from app.models.jobs import JobStatus
from app.models.processing_jobs import ProcessingJobStatus
from app.repositories.jobs import JobRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.schemas.jobs import JobWriteRequest
from app.services.job_publication_content import JobPublicationContentBuilder
from app.services.job_publications import JobPublishingService
from app.services.jobs import JobService


def approve_job(
    db_session: Session,
    job_payload: dict[str, Any],
    *,
    status: JobStatus = JobStatus.APPROVED,
) -> UUID:
    repository = JobRepository(db_session)
    job = JobService(repository).create_job(JobWriteRequest.model_validate(job_payload))
    persisted = repository.get(job.id)
    assert persisted is not None
    persisted.jd_generated_content = (
        "## Summary\nBuild dependable products for our customers.\n\n"
        "## Responsibilities\nShip APIs."
    )
    persisted.jd_content = persisted.jd_generated_content
    persisted.jd_version = 1
    persisted.status = status
    persisted.approved_at = datetime.now(UTC) if status == JobStatus.APPROVED else None
    repository.update(persisted)
    return job.id


def publishing_service(db_session: Session, publisher) -> JobPublishingService:
    return build_job_publishing_service(db_session, publisher)


async def test_publishing_service_persists_success_and_blocks_duplicate(
    db_session: Session,
    fake_publisher_adapter,
    job_payload: dict[str, Any],
) -> None:
    job_id = approve_job(db_session, job_payload)
    service = publishing_service(db_session, fake_publisher_adapter)

    queued = service.publish(job_id)

    assert queued.status == PublicationStatus.QUEUED
    assert queued.attempt_number == 1
    assert JobRepository(db_session).get(job_id).status == JobStatus.PUBLISHING
    with pytest.raises(InvalidJobStatusError, match="already being published"):
        service.publish(job_id)

    progress: list[tuple[int, str]] = []
    await service.process(queued.id, lambda value, message: progress.append((value, message)))

    published = service.get_latest(job_id)
    assert published is not None
    assert published.status == PublicationStatus.PUBLISHED
    assert published.external_post_uri.startswith("at://")
    assert published.external_url is not None
    assert len(fake_publisher_adapter.contents) == 1
    assert progress[-1] == (90, "Bluesky publication result saved")


async def test_publishing_failure_is_safe_and_explicit_retry_succeeds(
    db_session: Session,
    fake_publisher_adapter,
    job_payload: dict[str, Any],
) -> None:
    job_id = approve_job(db_session, job_payload)
    service = publishing_service(db_session, fake_publisher_adapter)
    fake_publisher_adapter.errors.append(PublisherUnavailableError("Bluesky is unavailable."))
    first = service.publish(job_id)

    with pytest.raises(PublisherUnavailableError):
        await service.process(first.id, lambda _progress, _message: None)

    failed = service.get_latest(job_id)
    assert failed is not None
    assert failed.status == PublicationStatus.FAILED
    assert failed.error_code == "PUBLISHER_UNAVAILABLE"
    assert JobRepository(db_session).get(job_id).status == JobStatus.PUBLISH_FAILED
    with pytest.raises(InvalidJobStatusError, match="explicit retry"):
        service.publish(job_id)

    retry = service.retry(job_id)
    assert retry.attempt_number == 2
    await service.process(retry.id, lambda _progress, _message: None)
    assert service.get_latest(job_id).status == PublicationStatus.PUBLISHED


@pytest.mark.parametrize(
    "job_status",
    [JobStatus.DRAFT, JobStatus.GENERATED, JobStatus.PUBLISHING, JobStatus.PUBLISHED],
)
def test_publishing_rejects_invalid_lifecycle_states(
    db_session: Session,
    fake_publisher_adapter,
    job_payload: dict[str, Any],
    job_status: JobStatus,
) -> None:
    job_id = approve_job(db_session, job_payload, status=job_status)

    with pytest.raises(InvalidJobStatusError):
        publishing_service(db_session, fake_publisher_adapter).publish(job_id)


def test_content_builder_uses_approved_job_without_regeneration(
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job_id = approve_job(db_session, job_payload)
    job = JobService(JobRepository(db_session)).get_job(job_id)

    content = JobPublicationContentBuilder().build(job)

    assert "Backend Engineer" in content.text
    assert "Build dependable products" in content.text
    assert "Python" in content.text
    assert "jobs@example.com" in content.text
    assert len(content.text) <= 300


async def test_bluesky_publisher_maps_official_api_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("createSession"):
            return httpx.Response(200, json={"did": "did:plc:test", "accessJwt": "token"})
        return httpx.Response(
            200,
            json={
                "uri": "at://did:plc:test/app.bsky.feed.post/3m3test",
                "cid": "bafytest",
            },
        )

    adapter = BlueskyPublisher(
        identifier="recruiter.example.com",
        app_password="test-password",
        transport=httpx.MockTransport(handler),
    )
    content = JobPublicationContent(text="We're hiring.", created_at=datetime.now(UTC))

    result = await adapter.publish_job(content)

    assert result.external_post_uri.endswith("/3m3test")
    assert result.external_record_id == "bafytest"
    assert result.external_url == "https://bsky.app/profile/did:plc:test/post/3m3test"
    assert len(requests) == 2
    assert requests[1].headers["Authorization"] == "Bearer token"


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (
            httpx.Response(401, json={"error": "AuthenticationRequired"}),
            PublisherAuthenticationError,
        ),
        (httpx.Response(503, json={"error": "Unavailable"}), PublisherUnavailableError),
    ],
)
async def test_bluesky_publisher_translates_provider_errors(
    response: httpx.Response,
    error_type: type[Exception],
) -> None:
    adapter = BlueskyPublisher(
        identifier="recruiter.example.com",
        app_password="test-password",
        transport=httpx.MockTransport(lambda _request: response),
    )

    with pytest.raises(error_type):
        await adapter.publish_job(
            JobPublicationContent(text="We're hiring.", created_at=datetime.now(UTC))
        )


async def test_bluesky_publisher_rejects_malformed_success() -> None:
    responses = iter(
        [
            httpx.Response(200, json={"did": "did:plc:test", "accessJwt": "token"}),
            httpx.Response(200, json={"cid": "missing-uri"}),
        ]
    )
    adapter = BlueskyPublisher(
        identifier="recruiter.example.com",
        app_password="test-password",
        transport=httpx.MockTransport(lambda _request: next(responses)),
    )

    with pytest.raises(PublisherInvalidResponseError):
        await adapter.publish_job(
            JobPublicationContent(text="We're hiring.", created_at=datetime.now(UTC))
        )


async def test_bluesky_publisher_requires_credentials_without_exposing_them() -> None:
    adapter = BlueskyPublisher(identifier="", app_password="")

    with pytest.raises(PublisherConfigurationError) as failure:
        await adapter.publish_job(
            JobPublicationContent(text="We're hiring.", created_at=datetime.now(UTC))
        )

    assert failure.value.code == "PUBLISHER_NOT_CONFIGURED"
    assert "password" not in str(failure.value).casefold()


def test_publish_api_queues_once_and_exposes_status(
    client: TestClient,
    db_session: Session,
    fake_publisher_adapter,
    job_payload: dict[str, Any],
) -> None:
    job_id = approve_job(db_session, job_payload)

    response = client.post(f"/api/v1/jobs/{job_id}/publish")

    assert response.status_code == 202
    publication = response.json()
    assert publication["status"] == "QUEUED"
    assert client.get(f"/api/v1/jobs/{job_id}/publication").json()["id"] == publication["id"]
    duplicate = client.post(f"/api/v1/jobs/{job_id}/publish")
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "INVALID_JOB_STATUS"

    task = ProcessingJobRepository(db_session).get(UUID(publication["processing_task_id"]))
    assert task is not None
    assert task.status == ProcessingJobStatus.QUEUED
    assert task.max_attempts == 1


async def test_api_exposes_safe_failure_and_allows_explicit_retry(
    client: TestClient,
    db_session: Session,
    fake_publisher_adapter,
    job_payload: dict[str, Any],
) -> None:
    job_id = approve_job(db_session, job_payload)
    fake_publisher_adapter.errors.append(PublisherUnavailableError("Bluesky is unavailable."))
    queued = client.post(f"/api/v1/jobs/{job_id}/publish").json()
    service = publishing_service(db_session, fake_publisher_adapter)

    with pytest.raises(PublisherUnavailableError):
        await service.process(UUID(queued["id"]), lambda _progress, _message: None)

    failed = client.get(f"/api/v1/jobs/{job_id}/publication")
    assert failed.status_code == 200
    assert failed.json()["status"] == "FAILED"
    assert failed.json()["error_message_safe"] == "Bluesky is unavailable."
    retry = client.post(f"/api/v1/jobs/{job_id}/publication/retry")
    assert retry.status_code == 202
    assert retry.json()["attempt_number"] == 2


def test_publication_api_returns_safe_not_found_for_unknown_job(client: TestClient) -> None:
    response = client.get(f"/api/v1/jobs/{UUID(int=0)}/publication")

    assert response.status_code == 404
    assert response.json()["code"] == "JOB_NOT_FOUND"


def test_job_page_shows_publish_action_for_approved_job(
    client: TestClient,
    db_session: Session,
    job_payload: dict[str, Any],
) -> None:
    job_id = approve_job(db_session, job_payload)

    response = client.get(f"/jobs/{job_id}")

    assert response.status_code == 200
    assert 'id="publish-job"' in response.text
    assert "Bluesky publication" in response.text
