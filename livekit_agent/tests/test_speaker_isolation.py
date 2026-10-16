import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from livekit import rtc
from livekit.agents import UserInputTranscribedEvent
from livekit.agents.voice.room_io._input import _ParticipantAudioInputStream

import agent


@pytest.mark.asyncio
async def test_publisher_jobs_keep_overlapping_transcripts_and_callbacks_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interview_id = uuid4()
    identities = [f"candidate_{interview_id.hex}", f"interviewer_{interview_id.hex}"]
    sessions = [MagicMock(), MagicMock()]
    callbacks = [SimpleNamespace(persist=AsyncMock(), aclose=AsyncMock()) for _ in identities]
    handlers = [{}, {}]
    contexts = []
    for identity, session, registered in zip(identities, sessions, handlers, strict=True):
        session.on.side_effect = lambda event, registered=registered: (
            lambda handler: registered.setdefault(event, handler)
        )
        session.start = AsyncMock()
        ctx = MagicMock()
        ctx.job.metadata = json.dumps(
            {
                "interview_id": str(interview_id),
                "callback_url": "http://app:8000/callback",
            }
        )
        ctx.job.participant.identity = identity
        ctx.room.name = f"ru_{interview_id.hex}"
        ctx.room.local_participant.send_text = AsyncMock()
        ctx.connect = AsyncMock()
        contexts.append(ctx)

    monkeypatch.setenv("STT_AGENT_INGEST_SECRET", "synthetic-secret")
    monkeypatch.setattr(agent, "AgentSession", MagicMock(side_effect=sessions))
    monkeypatch.setattr(agent, "TranscriptionCallbackClient", MagicMock(side_effect=callbacks))
    stt_factory = MagicMock(side_effect=[object(), object()])
    monkeypatch.setattr(
        agent,
        "AssemblyAIStreamingSTTAdapter",
        lambda: SimpleNamespace(create=stt_factory, provider="ASSEMBLYAI"),
    )

    await asyncio.gather(*(agent.transcribe_publisher(ctx) for ctx in contexts))
    for identity, session in zip(identities, sessions, strict=True):
        options = session.start.call_args.kwargs
        assert options["room_options"].participant_identity == identity
        assert options["record"] is False
    assert (
        sessions[0].start.call_args.kwargs["room_options"]
        is not (sessions[1].start.call_args.kwargs["room_options"])
    )

    # Interleave interim/final events with the same provider ID in both jobs.
    # Provider speaker IDs deliberately disagree: they must never determine roles.
    for is_final in (False, True):
        for index in (1, 0):
            handlers[index]["user_input_transcribed"](
                UserInputTranscribedEvent(
                    transcript=f"Synthetic speaker {index} answer",
                    is_final=is_final,
                    item_id="shared-segment-id",
                    speaker_id=identities[1 - index],
                )
            )
    await asyncio.gather(*(ctx.add_shutdown_callback.call_args.args[0]() for ctx in contexts))

    for index, callback in enumerate(callbacks):
        callback.persist.assert_awaited_once()
        segment = callback.persist.call_args.args[0]
        assert segment.participant_identity == identities[index]
        assert segment.text == f"Synthetic speaker {index} answer"
        assert segment.segment_key == "shared-segment-id"
        callback.aclose.assert_awaited_once()


class SyntheticAudioStream:
    def __init__(self) -> None:
        self.frames: asyncio.Queue[rtc.AudioFrame] = asyncio.Queue()

    def __aiter__(self) -> "SyntheticAudioStream":
        return self

    async def __anext__(self) -> SimpleNamespace:
        return SimpleNamespace(frame=await self.frames.get())

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_livekit_microphone_input_rejects_other_speaker_and_keeps_replacement_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the pinned SDK input filter, with synthetic frames and no live room."""
    room = MagicMock(remote_participants={})
    inputs = [
        _ParticipantAudioInputStream(
            room,
            sample_rate=16000,
            num_channels=1,
            noise_cancellation=None,
            auto_gain_control=False,
            pre_connect_audio_handler=None,
        )
        for _ in range(2)
    ]
    identities = [f"candidate_{uuid4().hex}", f"interviewer_{uuid4().hex}"]
    participants = [SimpleNamespace(identity=identity) for identity in identities]
    audio_streams: dict[str, SyntheticAudioStream] = {}

    def create_stream(*, track: SimpleNamespace, **_kwargs: object) -> SyntheticAudioStream:
        stream = SyntheticAudioStream()
        audio_streams[track.sid] = stream
        return stream

    monkeypatch.setattr(rtc.AudioStream, "from_track", create_stream)
    tracks = [SimpleNamespace(sid=f"microphone-{index}") for index in range(2)]
    publications = [
        SimpleNamespace(sid=track.sid, source=rtc.TrackSource.SOURCE_MICROPHONE) for track in tracks
    ]
    frames = [rtc.AudioFrame(bytes([index + 1, 0]) * 160, 16000, 1, 160) for index in range(2)]
    try:
        for index, audio_input in enumerate(inputs):
            audio_input.set_participant(identities[index])
            other = 1 - index
            assert not audio_input._on_track_available(
                tracks[other], publications[other], participants[other]
            )
            assert not audio_input._on_track_available(
                tracks[index],
                SimpleNamespace(sid="screen", source=rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO),
                participants[index],
            )
            assert audio_input._on_track_available(
                tracks[index], publications[index], participants[index]
            )

        # Both microphones deliver frames during the same interval.
        for track, frame in zip(tracks, frames, strict=True):
            audio_streams[track.sid].frames.put_nowait(frame)
        received = await asyncio.wait_for(
            asyncio.gather(*(anext(audio_input) for audio_input in inputs)), timeout=2
        )
        assert received == frames

        replacement = SimpleNamespace(sid="candidate-microphone-reconnected")
        replacement_publication = SimpleNamespace(
            sid=replacement.sid, source=rtc.TrackSource.SOURCE_MICROPHONE
        )
        assert not inputs[1]._on_track_available(
            replacement, replacement_publication, participants[0]
        )
        assert inputs[0]._on_track_available(replacement, replacement_publication, participants[0])
        # A late unsubscribe for the old track must not detach the replacement.
        inputs[0]._on_track_unsubscribed(tracks[0], publications[0], participants[0])
        audio_streams[replacement.sid].frames.put_nowait(frames[0])
        audio_streams[tracks[1].sid].frames.put_nowait(frames[1])
        assert (
            await asyncio.wait_for(
                asyncio.gather(*(anext(audio_input) for audio_input in inputs)), timeout=2
            )
            == frames
        )
    finally:
        await asyncio.gather(*(audio_input.aclose() for audio_input in inputs))
