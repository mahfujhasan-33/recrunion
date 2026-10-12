from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.dependencies import build_assistant_service
from app.models.assistant import AssistantArtifactStatus, AssistantArtifactType
from app.models.jobs import JobStatus
from app.models.processing_jobs import ProcessingJobStatus
from app.repositories.assistant import AssistantRepository
from app.repositories.jobs import JobRepository
from app.repositories.processing_jobs import ProcessingJobRepository
from app.schemas.jobs import JobWriteRequest
from app.services.jobs import JobService


def test_assistant_page_uses_split_workspace(client: TestClient) -> None:
    response = client.get("/assistant")

    assert response.status_code == 200
    assert "assistant-chat" in response.text
    assert "assistant-workspace" in response.text
    assert "progress-bar" in response.text
    assert "Required actions" not in response.text


def test_conversation_starts_with_requirements_and_required_actions(
    client: TestClient,
) -> None:
    response = client.post("/api/v1/assistant/conversations")

    assert response.status_code == 201
    body = response.json()
    assert body["messages"][0]["role"] == "ASSISTANT"
    assert body["workspace"]["requirements"]["artifact_type"] == "JOB_REQUIREMENTS"
    assert body["workspace"]["required_actions"][0]["code"] == "COMPLETE_REQUIREMENTS"


def test_chat_turn_is_queued_with_meaningful_progress_message(client: TestClient) -> None:
    conversation = client.post("/api/v1/assistant/conversations").json()

    response = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/messages",
        json={"content": "I need an AI/ML engineer."},
    )

    assert response.status_code == 202
    task = client.get(f"/api/v1/tasks/{response.json()['task_id']}").json()
    assert task["status"] == "QUEUED"
    assert task["progress"] == 0
    assert task["progress_message"] == "Waiting for the recruiter assistant"


async def test_assistant_processes_requirement_draft(
    client: TestClient,
    db_session: Session,
    fake_llm_adapter,
    fake_embedding_adapter,
) -> None:
    conversation = client.post("/api/v1/assistant/conversations").json()
    queued = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/messages",
        json={"content": "I need an AI/ML engineer."},
    ).json()
    task_repository = ProcessingJobRepository(db_session)
    task = task_repository.get(UUID(queued["task_id"]))
    assert task is not None
    service = build_assistant_service(db_session, fake_llm_adapter, fake_embedding_adapter)

    await service.process_turn(
        task.entity_id,
        lambda progress, message: task_repository.update_progress(task, progress, message),
    )
    task_repository.complete(task)

    updated = client.get(f"/api/v1/assistant/conversations/{conversation['id']}").json()
    assert updated["workspace"]["requirements"]["payload"]["title"] == "AI/ML Engineer"
    assert "location" in updated["workspace"]["requirements"]["validation"]["missing_fields"]
    completed_task = client.get(f"/api/v1/tasks/{queued['task_id']}").json()
    assert completed_task["status"] == ProcessingJobStatus.COMPLETED
    assert completed_task["progress"] == 100
    assert completed_task["progress_message"] == "Completed"


async def test_assistant_generates_job_through_existing_jd_service(
    client: TestClient,
    db_session: Session,
    fake_llm_adapter,
    fake_embedding_adapter,
    job_payload,
) -> None:
    conversation = client.post("/api/v1/assistant/conversations").json()
    artifact_id = conversation["workspace"]["requirements"]["id"]
    saved = client.put(
        f"/api/v1/assistant/conversations/{conversation['id']}/artifacts/{artifact_id}",
        json={"payload": job_payload},
    )
    assert saved.status_code == 200
    queued = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/messages",
        json={"content": "Generate the job description now."},
    ).json()
    task_repository = ProcessingJobRepository(db_session)
    task = task_repository.get(UUID(queued["task_id"]))
    assert task is not None
    service = build_assistant_service(db_session, fake_llm_adapter, fake_embedding_adapter)

    await service.process_turn(
        task.entity_id,
        lambda progress, message: task_repository.update_progress(task, progress, message),
    )
    task_repository.complete(task)

    updated = client.get(f"/api/v1/assistant/conversations/{conversation['id']}").json()
    assert updated["workspace"]["job"]["status"] == "GENERATED"
    assert "Backend Engineer" in updated["workspace"]["job"]["jd_content"]
    assert updated["workspace"]["policy_review"]["is_current"] is True

    proposal = AssistantRepository(db_session).save_artifact(
        conversation_id=UUID(conversation["id"]),
        artifact_type=AssistantArtifactType.JOB_DESCRIPTION_PROPOSAL,
        payload={
            "content": updated["workspace"]["job"]["jd_content"]
            + "\n\n## Company policy\nFollow the documented hiring policy."
        },
        status=AssistantArtifactStatus.PROPOSED,
        job_id=UUID(updated["workspace"]["job"]["id"]),
        base_job_version=updated["workspace"]["job"]["jd_version"],
    )
    applied = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/proposals/{proposal.id}/apply"
    )

    assert applied.status_code == 200
    applied_workspace = applied.json()["workspace"]
    assert applied_workspace["job"]["jd_version"] == 2
    assert applied_workspace["proposal"]["status"] == "APPLIED"
    assert applied_workspace["policy_review"]["is_current"] is False


async def test_assistant_proposes_publication_and_requires_confirmation(
    client: TestClient,
    db_session: Session,
    fake_llm_adapter,
    fake_embedding_adapter,
    fake_publisher_adapter,
    job_payload,
) -> None:
    conversation = client.post("/api/v1/assistant/conversations").json()
    job_repository = JobRepository(db_session)
    job = JobService(job_repository).create_job(JobWriteRequest.model_validate(job_payload))
    persisted = job_repository.get(job.id)
    assert persisted is not None
    persisted.jd_content = "## Summary\nAn approved role description with enough detail for review."
    persisted.jd_generated_content = persisted.jd_content
    persisted.jd_version = 3
    persisted.status = JobStatus.APPROVED
    persisted.approved_at = datetime.now(UTC)
    job_repository.update(persisted)
    conversation_model = AssistantRepository(db_session).get_conversation(UUID(conversation["id"]))
    assert conversation_model is not None
    conversation_model.active_job_id = job.id
    AssistantRepository(db_session).update_conversation(conversation_model)

    queued = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/messages",
        json={"content": "Publish this job."},
    ).json()
    task_repository = ProcessingJobRepository(db_session)
    task = task_repository.get(UUID(queued["task_id"]))
    assert task is not None
    service = build_assistant_service(
        db_session,
        fake_llm_adapter,
        fake_embedding_adapter,
        fake_publisher_adapter,
    )

    await service.process_turn(
        task.entity_id,
        lambda progress, message: task_repository.update_progress(task, progress, message),
    )

    before_confirmation = client.get(f"/api/v1/assistant/conversations/{conversation['id']}").json()
    assert before_confirmation["workspace"]["job"]["status"] == "APPROVED"
    assert before_confirmation["workspace"]["publication"] is None
    assert before_confirmation["workspace"]["required_actions"][0]["code"] == "PUBLISH_JOB"
    assert "ready to publish" in before_confirmation["messages"][-1]["content"]

    declined = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/publish",
        json={"confirmed": False},
    )
    assert declined.status_code == 409
    assert JobService(job_repository).get_job(job.id).status == "APPROVED"

    confirmed = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/publish",
        json={"confirmed": True},
    )
    assert confirmed.status_code == 202
    assert confirmed.json()["status"] == "QUEUED"
    db_session.expire_all()
    assert JobService(job_repository).get_job(job.id).status == "PUBLISHING"
    repeated = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/publish",
        json={"confirmed": True},
    )
    assert repeated.status_code == 409


async def test_assistant_blocks_publish_for_unapproved_active_job(
    client: TestClient,
    db_session: Session,
    fake_llm_adapter,
    fake_embedding_adapter,
    fake_publisher_adapter,
    job_payload,
) -> None:
    conversation = client.post("/api/v1/assistant/conversations").json()
    job = JobService(JobRepository(db_session)).create_job(
        JobWriteRequest.model_validate(job_payload)
    )
    repository = AssistantRepository(db_session)
    conversation_model = repository.get_conversation(UUID(conversation["id"]))
    assert conversation_model is not None
    conversation_model.active_job_id = job.id
    repository.update_conversation(conversation_model)
    queued = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/messages",
        json={"content": "Publish this job."},
    ).json()
    task_repository = ProcessingJobRepository(db_session)
    task = task_repository.get(UUID(queued["task_id"]))
    assert task is not None

    await build_assistant_service(
        db_session,
        fake_llm_adapter,
        fake_embedding_adapter,
        fake_publisher_adapter,
    ).process_turn(
        task.entity_id,
        lambda progress, message: task_repository.update_progress(task, progress, message),
    )

    state = client.get(f"/api/v1/assistant/conversations/{conversation['id']}").json()
    assert any("still a draft" in message["content"] for message in state["messages"])
    rejected = client.post(
        f"/api/v1/assistant/conversations/{conversation['id']}/publish",
        json={"confirmed": True},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "INVALID_JOB_STATUS"
