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
