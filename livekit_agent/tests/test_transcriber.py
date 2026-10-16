from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from recrunion_transcriber.callback import (
    FinalTranscriptSegment,
    TranscriptionCallbackClient,
)
from recrunion_transcriber.session import (
    DispatchMetadata,
    TranscriptSegmentTracker,
    publisher_role,
)
from recrunion_transcriber.stt import ASSEMBLYAI_MODEL, AssemblyAIStreamingSTTAdapter


def test_dispatch_metadata_and_human_publisher_filtering() -> None:
    interview_id = uuid4()
    metadata = DispatchMetadata.parse(
        f'{{"interview_id":"{interview_id}",'
        '"callback_url":"https://recrunion.example.test/callback"}'
    )

    assert metadata.interview_id == interview_id
    private_metadata = DispatchMetadata.parse(
        f'{{"interview_id":"{interview_id}","callback_url":"http://app:8000/callback"}}'
    )
    assert private_metadata.callback_url == "http://app:8000/callback"
    assert publisher_role(interview_id, f"candidate_{interview_id.hex}") == "CANDIDATE"
    assert publisher_role(interview_id, f"interviewer_{interview_id.hex}") == "INTERVIEWER"
    assert publisher_role(interview_id, "agent-service") is None
    assert publisher_role(interview_id, "candidate_wrong") is None

    with pytest.raises(ValueError, match="HTTPS"):
        DispatchMetadata.parse(
            f'{{"interview_id":"{interview_id}","callback_url":"http://localhost/callback"}}'
        )
    with pytest.raises(ValueError, match="HTTPS"):
        DispatchMetadata.parse(
            f'{{"interview_id":"{interview_id}",'
            '"callback_url":"http://app:8000/callback?secret=unsafe"}'
        )


def test_tracker_ignores_empty_and_uses_interim_start_for_final() -> None:
    tracker = TranscriptSegmentTracker()
    start = datetime.now(UTC)
    assert (
        tracker.normalize(
            room_name="room",
            participant_identity="candidate_id",
            transcript="   ",
            is_final=True,
            item_id="segment-1",
            language="en",
            provider="ASSEMBLYAI",
            now=start,
        )
        is None
    )
    assert (
        tracker.normalize(
            room_name="room",
            participant_identity="candidate_id",
            transcript="Partial",
            is_final=False,
            item_id="segment-1",
            language="en",
            provider="ASSEMBLYAI",
            now=start,
        )
        is None
    )
    final = tracker.normalize(
        room_name="room",
        participant_identity="candidate_id",
        transcript="Final answer",
        is_final=True,
        item_id="segment-1",
        language="en",
        provider="ASSEMBLYAI",
        now=start + timedelta(seconds=3),
    )

    assert final is not None
    assert final.started_at == start
    assert final.ended_at == start + timedelta(seconds=3)
    assert final.segment_key == "segment-1"


def test_assemblyai_adapter_uses_explicit_model_without_diarization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_stt(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "synthetic-key")
    monkeypatch.setattr("recrunion_transcriber.stt.assemblyai.STT", fake_stt)
    adapter = AssemblyAIStreamingSTTAdapter()

    adapter.create()

    assert adapter.provider == "ASSEMBLYAI"
    assert adapter.model == ASSEMBLYAI_MODEL == "universal-streaming-english"
    assert captured["model"] == "universal-streaming-english"
    assert "speaker_labels" not in captured


class FakeHTTPClient:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = statuses
        self.calls = []
        self.close_count = 0

    async def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        status = self.statuses.pop(0)
        return httpx.Response(status, request=httpx.Request("POST", url))

    async def aclose(self) -> None:
        self.close_count += 1


@pytest.mark.asyncio
async def test_callback_retries_and_uses_bearer_secret_without_logging_text(caplog) -> None:
    interview_id = uuid4()
    http_client = FakeHTTPClient([503, 200])
    delays = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    client = TranscriptionCallbackClient(
        callback_url="https://recrunion.example.test/callback",
        secret="synthetic-shared-secret",
        interview_id=interview_id,
        http_client=http_client,
        sleeper=fake_sleep,
    )
    segment = FinalTranscriptSegment(
        room_name=f"ru_{interview_id.hex}",
        participant_identity=f"candidate_{interview_id.hex}",
        segment_key="segment-1",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        text="private synthetic transcript text",
        language="en",
        provider="ASSEMBLYAI",
    )

    assert await client.persist(segment) is True
    assert len(http_client.calls) == 2
    assert delays == [0.5]
    assert http_client.calls[0][1]["headers"] == {"Authorization": "Bearer synthetic-shared-secret"}
    assert "private synthetic transcript text" not in caplog.text


@pytest.mark.asyncio
async def test_callback_reuses_and_closes_one_owned_async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_client = FakeHTTPClient([200, 200])
    created_clients = []

    def fake_async_client() -> FakeHTTPClient:
        created_clients.append(http_client)
        return http_client

    monkeypatch.setattr(
        "recrunion_transcriber.callback.httpx.AsyncClient",
        fake_async_client,
    )
    interview_id = uuid4()
    client = TranscriptionCallbackClient(
        callback_url="https://recrunion.example.test/callback",
        secret="synthetic-shared-secret",
        interview_id=interview_id,
    )

    def segment(segment_key: str) -> FinalTranscriptSegment:
        return FinalTranscriptSegment(
            room_name=f"ru_{interview_id.hex}",
            participant_identity=f"candidate_{interview_id.hex}",
            segment_key=segment_key,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            text="private synthetic transcript text",
            language="en",
            provider="ASSEMBLYAI",
        )

    assert await client.persist(segment("segment-1")) is True
    assert await client.persist(segment("segment-2")) is True
    await client.aclose()
    await client.aclose()

    assert created_clients == [http_client]
    assert len(http_client.calls) == 2
    assert http_client.close_count == 1


def test_agent_is_publisher_scoped_stt_only_and_disables_recording() -> None:
    source = Path(__file__).parents[1].joinpath("agent.py").read_text(encoding="utf-8")

    assert "type=ServerType.PUBLISHER" in source
    assert "ctx.job.participant.identity" in source
    assert "ctx.publisher" not in source
    assert "participant_identity=publisher_identity" in source
    connect_call = "await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)"
    assert connect_call in source
    assert source.index(connect_call) < source.index("await session.start(")
    assert "video_input=False" in source
    assert "audio_output=False" in source
    assert "text_output=True" in source
    assert "record=False" in source
    assert "await callback.aclose()" in source
    assert "llm=" not in source
    assert "tts=" not in source
