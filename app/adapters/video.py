from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class VideoParticipantRole(StrEnum):
    CANDIDATE = "CANDIDATE"
    INTERVIEWER = "INTERVIEWER"


@dataclass(frozen=True)
class VideoParticipantCredentials:
    provider_url: str
    participant_token: str
    room_name: str
    participant_identity: str


@dataclass(frozen=True)
class VideoParticipant:
    identity: str


class VideoAdapter(Protocol):
    @property
    def configured(self) -> bool:
        """Return whether the provider has complete server-side configuration."""

    def create_participant_credentials(
        self,
        interview_id: UUID,
        role: VideoParticipantRole,
    ) -> VideoParticipantCredentials:
        """Create short-lived credentials for one server-selected participant."""

    async def list_participants(self, interview_id: UUID) -> list[VideoParticipant]:
        """Return normalized participants currently present in the provider room."""

    async def terminate_room(self, interview_id: UUID) -> None:
        """Disconnect participants and remove the provider room."""


def video_room_name(interview_id: UUID) -> str:
    return f"ru_{interview_id.hex}"


def video_participant_identity(
    interview_id: UUID,
    role: VideoParticipantRole,
) -> str:
    return f"{role.value.casefold()}_{interview_id.hex}"
