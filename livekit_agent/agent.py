import asyncio
import logging
import os
from collections.abc import Coroutine
from typing import Any

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AutoSubscribe,
    JobContext,
    UserInputTranscribedEvent,
    cli,
    room_io,
)
from livekit.agents.worker import ServerType

from recrunion_transcriber.callback import TranscriptionCallbackClient
from recrunion_transcriber.session import (
    DispatchMetadata,
    TranscriptSegmentTracker,
    publisher_role,
)
from recrunion_transcriber.stt import AssemblyAIStreamingSTTAdapter

logger = logging.getLogger(__name__)
server = AgentServer()


async def publish_status(ctx: JobContext, status: str) -> None:
    try:
        await ctx.room.local_participant.send_text(
            status,
            topic="recrunion.transcription.status",
        )
    except Exception:
        logger.warning("Transcription status could not be published room=%s", ctx.room.name)


@server.rtc_session(agent_name="recrunion-transcriber", type=ServerType.PUBLISHER)
async def transcribe_publisher(ctx: JobContext) -> None:
    metadata = DispatchMetadata.parse(ctx.job.metadata)
    publisher_identity = ctx.job.participant.identity
    if not publisher_identity or publisher_role(metadata.interview_id, publisher_identity) is None:
        logger.warning("Ignoring unexpected transcription publisher room=%s", ctx.room.name)
        return

    secret = os.getenv("STT_AGENT_INGEST_SECRET", "").strip()
    if not secret:
        logger.error("Transcript callback authentication is not configured room=%s", ctx.room.name)
        return

    adapter = AssemblyAIStreamingSTTAdapter()
    callback = TranscriptionCallbackClient(
        callback_url=metadata.callback_url,
        secret=secret,
        interview_id=metadata.interview_id,
    )
    tracker = TranscriptSegmentTracker()
    session = AgentSession(stt=adapter.create())
    background_tasks: set[asyncio.Task[object]] = set()

    def start_background(coroutine: Coroutine[Any, Any, object]) -> None:
        task = asyncio.create_task(coroutine)
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    async def drain_background_tasks() -> None:
        if background_tasks:
            await asyncio.gather(*tuple(background_tasks), return_exceptions=True)

    async def close_callback_client() -> None:
        try:
            await drain_background_tasks()
        finally:
            await callback.aclose()

    ctx.add_shutdown_callback(close_callback_client)

    @session.on("user_input_transcribed")
    def on_transcript(event: UserInputTranscribedEvent) -> None:
        segment = tracker.normalize(
            room_name=ctx.room.name,
            participant_identity=publisher_identity,
            transcript=event.transcript,
            is_final=event.is_final,
            item_id=event.item_id,
            language=str(event.language) if event.language else None,
            provider=adapter.provider,
        )
        if segment is not None:
            start_background(callback.persist(segment))

    @session.on("error")
    def on_error(_event: object) -> None:
        start_background(publish_status(ctx, "UNAVAILABLE"))

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    await session.start(
        agent=Agent(instructions="Transcribe the linked interview participant only."),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=publisher_identity,
            text_input=False,
            audio_input=True,
            video_input=False,
            audio_output=False,
            text_output=True,
            close_on_disconnect=True,
        ),
        record=False,
    )
    await publish_status(ctx, "LIVE")


if __name__ == "__main__":
    cli.run_app(server)
