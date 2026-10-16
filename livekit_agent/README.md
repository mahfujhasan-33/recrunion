# RecrUnion transcription agent

This independently testable LiveKit publisher agent performs speech-to-text only. The active
evaluation runtime is the `transcription-agent` Docker Compose service. It connects outbound to
LiveKit Cloud and AssemblyAI and calls RecrUnion through the private Compose network. It has no LLM,
TTS, tools, video input, recording, Egress, public port, or raw-media persistence.

## Local preparation

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
```

Run unit tests from this directory with `pytest`. Runtime secrets come from the root `.env` through
Docker Compose; no local agent environment file is required for the active path.

## Active evaluation runtime

```bash
docker compose up -d --build --wait
docker compose ps
docker compose logs --tail=100 transcription-agent
```

Required root environment values:

```env
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
ASSEMBLYAI_API_KEY=
STT_AGENT_INGEST_SECRET=
LIVEKIT_TRANSCRIPTION_AGENT_NAME=recrunion-transcriber
STT_DISPATCH_ENABLED=true
STT_AGENT_CALLBACK_BASE_URL=http://app:8000
```

`APP_BASE_URL` remains the public HTTPS browser/invitation origin. The callback override is private
and does not replace it. The agent registers as `recrunion-transcriber`, waits for explicit
publisher dispatch, and exposes no host port.

## Speaker isolation and overlap verification

Each publisher job fixes `RoomOptions.participant_identity` to the server-dispatched candidate or
interviewer identity. The pinned LiveKit SDK accepts microphone frames only from that identity,
including after microphone replacement; it does not mix room audio. Each job owns its STT session,
segment tracker, and callback client. Backend roles derive from the exact participant identity,
and callback deduplication includes that identity, so overlapping speakers can use the same segment
ID without overwriting each other. Live rows also include the participant identity in their key.

Automated tests exercise concurrent publisher callbacks, opposing provider speaker IDs, identical
segment IDs, simultaneous synthetic audio frames, rejection of the other participant/screenshare
audio, and a replacement microphone with a late old-track unsubscribe. They verify routing and
persistence, not real microphone acoustics or provider recognition accuracy.

For live verification, use two devices in separate rooms and headphones on both sides:

1. Join a synthetic interview and say distinct phrases in turns. Check both live labels and the
   saved transcript after reloading the recruiter page.
2. Speak different phrases simultaneously for several seconds. Each phrase must stay under its
   originating participant; overlapping time intervals are valid.
3. Mute the candidate microphone and speak as the interviewer. No new candidate speech should
   appear after any already-buffered final segment. Repeat with the roles reversed.
4. Rejoin one participant, repeat the overlap check, and verify the saved labels remain correct.

Speaker labels identify the originating microphone, not a biometric voice identity. Loudspeaker
playback or two nearby devices can put one voice into both microphones; isolated streams cannot
undo that acoustic bleed. Headphones and physically separated participants are required to rule
it out when investigating apparent mislabelling. Raw audio recording remains disabled.

## Future production option

The same project can later move to LiveKit Cloud hosting for managed scaling or geographic
placement. That optional path uses the LiveKit CLI and a secret file containing only
`ASSEMBLYAI_API_KEY` and `STT_AGENT_INGEST_SECRET`:

```bash
lk cloud auth
lk agent create --secrets-file .env.local
# Later versions:
lk agent deploy
```

The agent name must remain `recrunion-transcriber`, or match the main application's
`LIVEKIT_TRANSCRIPTION_AGENT_NAME`.
