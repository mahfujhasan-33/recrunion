from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from app.dependencies import get_transcript_service
from app.schemas.transcripts import (
    InterviewTranscriptResponse,
    TranscriptSegmentIngestRequest,
    TranscriptSegmentIngestResponse,
)
from app.services.transcripts import TranscriptService

router = APIRouter(tags=["interview transcripts"])
TranscriptServiceDependency = Annotated[TranscriptService, Depends(get_transcript_service)]


@router.post(
    "/api/v1/internal/interviews/{interview_id}/transcript-segments",
    response_model=TranscriptSegmentIngestResponse,
)
def ingest_transcript_segment(
    interview_id: UUID,
    payload: TranscriptSegmentIngestRequest,
    service: TranscriptServiceDependency,
    authorization: Annotated[str | None, Header()] = None,
) -> TranscriptSegmentIngestResponse:
    return service.ingest(interview_id, payload, authorization)


@router.get(
    "/api/v1/interviews/{interview_id}/transcript",
    response_model=InterviewTranscriptResponse,
)
def get_interview_transcript(
    interview_id: UUID,
    service: TranscriptServiceDependency,
) -> InterviewTranscriptResponse:
    return service.get_transcript(interview_id)
