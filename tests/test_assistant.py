from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.dependencies import build_assistant_service
from app.models.assistant import AssistantArtifactStatus, AssistantArtifactType
from app.models.processing_jobs import ProcessingJobStatus
from app.repositories.assistant import AssistantRepository
from app.repositories.processing_jobs import ProcessingJobRepository


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
