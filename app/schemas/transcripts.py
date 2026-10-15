from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.interviews import TranscriptParticipantRole


class TranscriptSegmentIngestRequest(BaseModel):
    room_name: str = Field(min_length=1, max_length=100)
    participant_identity: str = Field(min_length=1, max_length=100)
    segment_key: str = Field(min_length=1, max_length=255)
    started_at: datetime
    ended_at: datetime
    text: str = Field(min_length=1, max_length=8000)
    language: str | None = Field(default=None, max_length=35)
    provider: str = Field(min_length=1, max_length=50)
    provider_segment_id: str | None = Field(default=None, max_length=255)

    @field_validator("text")
    @classmethod
    def trim_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Transcript text cannot be blank.")
        return value


class TranscriptSegmentResponse(BaseModel):
    id: UUID
    participant_role: TranscriptParticipantRole
    participant_identity: str
    segment_key: str
    started_at: datetime
    ended_at: datetime
    text: str
    language: str | None
    provider: str


class TranscriptSegmentIngestResponse(BaseModel):
    segment: TranscriptSegmentResponse
    created: bool


class InterviewTranscriptResponse(BaseModel):
    interview_id: UUID
    segments: list[TranscriptSegmentResponse]
