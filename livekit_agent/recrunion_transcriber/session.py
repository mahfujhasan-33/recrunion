import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID, uuid4

from recrunion_transcriber.callback import FinalTranscriptSegment


@dataclass(frozen=True)
class DispatchMetadata:
    interview_id: UUID
    callback_url: str

    @classmethod
    def parse(cls, raw: str) -> "DispatchMetadata":
        payload = json.loads(raw)
        interview_id = UUID(payload["interview_id"])
        callback_url = str(payload["callback_url"])
        parsed = urlparse(callback_url)
        public_https = parsed.scheme == "https" and bool(parsed.netloc)
        private_compose_http = (
            parsed.scheme == "http" and parsed.hostname == "app" and bool(parsed.netloc)
        )
        if (
            not (public_https or private_compose_http)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Transcript callback must use HTTPS or the internal app service.")
        return cls(interview_id=interview_id, callback_url=callback_url)


def publisher_role(interview_id: UUID, identity: str) -> str | None:
    expected_suffix = interview_id.hex
    if identity == f"candidate_{expected_suffix}":
        return "CANDIDATE"
    if identity == f"interviewer_{expected_suffix}":
        return "INTERVIEWER"
    return None


class TranscriptSegmentTracker:
    """Track wall-clock timing without inventing provider speech timestamps."""

    def __init__(self) -> None:
        self._started_at: dict[str, datetime] = {}

    def normalize(
        self,
        *,
        room_name: str,
        participant_identity: str,
        transcript: str,
        is_final: bool,
        item_id: str | None,
        language: str | None,
        provider: str,
        now: datetime | None = None,
    ) -> FinalTranscriptSegment | None:
        text = transcript.strip()
        if not text:
            return None
        event_time = now or datetime.now(UTC)
        tracking_key = item_id or "current"
        started_at = self._started_at.setdefault(tracking_key, event_time)
        if not is_final:
            return None
        self._started_at.pop(tracking_key, None)
        segment_key = item_id or str(uuid4())
        return FinalTranscriptSegment(
            room_name=room_name,
            participant_identity=participant_identity,
            segment_key=segment_key,
            started_at=started_at,
            ended_at=event_time,
            text=text,
            language=language,
            provider=provider,
            provider_segment_id=item_id,
        )
