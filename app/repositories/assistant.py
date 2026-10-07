from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.assistant import (
    AssistantArtifact,
    AssistantArtifactStatus,
    AssistantArtifactType,
    AssistantConversation,
    AssistantMessage,
)


class AssistantRepository:
    """Persist assistant conversations without owning business decisions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_conversation(self, conversation: AssistantConversation) -> AssistantConversation:
        self._session.add(conversation)
        self._commit()
        return conversation

    def get_conversation(self, conversation_id: UUID) -> AssistantConversation | None:
        return self._session.get(AssistantConversation, conversation_id)

    def latest_conversation(self) -> AssistantConversation | None:
        return self._session.scalar(
            select(AssistantConversation).order_by(AssistantConversation.updated_at.desc()).limit(1)
        )

    def update_conversation(self, conversation: AssistantConversation) -> AssistantConversation:
        self._commit()
        return conversation

    def add_message(self, message: AssistantMessage) -> AssistantMessage:
        self._session.add(message)
        self._commit()
        return message

    def get_message(self, message_id: UUID) -> AssistantMessage | None:
        return self._session.get(AssistantMessage, message_id)

    def list_messages(self, conversation_id: UUID) -> list[AssistantMessage]:
        return list(
            self._session.scalars(
                select(AssistantMessage)
                .where(AssistantMessage.conversation_id == conversation_id)
                .order_by(AssistantMessage.created_at, AssistantMessage.id)
            )
        )

    def get_artifact(self, artifact_id: UUID) -> AssistantArtifact | None:
        return self._session.get(AssistantArtifact, artifact_id)

    def latest_artifact(
        self,
        conversation_id: UUID,
        artifact_type: AssistantArtifactType,
    ) -> AssistantArtifact | None:
        return self._session.scalar(
            select(AssistantArtifact)
            .where(
                AssistantArtifact.conversation_id == conversation_id,
                AssistantArtifact.artifact_type == artifact_type,
            )
            .order_by(AssistantArtifact.updated_at.desc(), AssistantArtifact.created_at.desc())
            .limit(1)
        )

    def save_artifact(
        self,
        *,
        conversation_id: UUID,
        artifact_type: AssistantArtifactType,
        payload: dict[str, object],
        status: AssistantArtifactStatus,
        job_id: UUID | None = None,
        base_job_version: int | None = None,
        validation: dict[str, object] | None = None,
    ) -> AssistantArtifact:
        artifact = self.latest_artifact(conversation_id, artifact_type)
        if artifact is None or artifact.status in {
            AssistantArtifactStatus.APPLIED,
            AssistantArtifactStatus.FAILED,
        }:
            artifact = AssistantArtifact(
                conversation_id=conversation_id,
                artifact_type=artifact_type,
            )
            self._session.add(artifact)
        artifact.payload = payload
        artifact.status = status
        artifact.job_id = job_id
        artifact.base_job_version = base_job_version
        artifact.validation = validation or {}
        self._commit()
        return artifact

    def update_artifact(self, artifact: AssistantArtifact) -> AssistantArtifact:
        self._commit()
        return artifact

    def _commit(self) -> None:
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            raise
