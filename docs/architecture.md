# RecrUnion Architecture

## Objective
Use the simplest architecture that still satisfies all six mandatory core features, F11, traceability, privacy, and clean Docker execution.

## Runtime
```text
Candidate Browser -------- encrypted audio/video -------- LiveKit Cloud
        |                                                   ^
        |                                                   |
        +--> RecrUnion capability validation                |
                                                            |
Interviewer Browser ------ encrypted audio/video -----------+
        |
        +--> RecrUnion recruiter room authorization

FastAPI Web Application
(Jinja2 + HTML/CSS/Vanilla JS)
        |
        +--> Application Services
        +--> processing_jobs --> Python Worker --> LangGraph
        +--> Adapter Layer --> Gemini / Ollama-Nomic / LiveKit / Bluesky / Gmail
        +--> PostgreSQL + pgvector

F4 transcription path:
LiveKit room --> Docker transcription-agent --> AssemblyAI --> RecrUnion transcript persistence
```

Current containers:
1. `app`
2. `worker`
3. `postgres`
4. `transcription-agent`

LiveKit Cloud is the external F3 realtime-media provider and owns signalling, WebRTC transport,
ICE/STUN/TURN, NAT traversal, and reconnection. RecrUnion owns participant authorization,
`InterviewSession` lifecycle, and persistence. FastAPI never proxies or records interview media.
Exactly one authorized candidate and one authorized interviewer are supported; provider room
capacity remains available for the hidden F4 transcription agent.

Baseline excludes Nginx, Redis, Celery, ChromaDB, Kubernetes, and microservices.

## Business flow
`Job Management → AI JD → Human Approval → Job Publishing → F1 CV Intelligence → F2 Candidate Scoring → F11 Probes → F3 Interview → F4 Transcript → F5 Analysis → F6 Assessment → Human Hiring Decision → Notification`

## Backend layering
```text
Router -> Service -> Repository / Adapter / Graph
```

- Routers: HTTP only
- Services: use cases
- Repositories: DB persistence
- Adapters: external providers
- Graphs: stateful multi-step reasoning only

## Planned graphs

### JD Graph
`load requirements -> validate -> build retrieval query -> retrieve company policy -> generate -> validate -> evaluate policy alignment -> validate evidence references -> persist draft and review`

An M2 enhancement graph handles recruiter-requested policy remediation:

`load current JD -> retrieve fresh policy evidence -> evaluate current alignment -> select actionable findings -> enhance -> validate -> re-evaluate -> persist new JD version and review`

Human approval happens outside the graph.

### Recruiter Assistant Graph
`interpret recruiter intent -> enforce typed action boundary -> service executes approved use case -> persist message/workspace artifact`

The assistant is a bounded orchestration layer, not a second implementation of job management. It calls existing services in-process. Requirements, current JD, enhancement proposal, evidence, publication state, and required actions are rendered from persisted state in a split-screen Jinja2/Vanilla JavaScript workspace. Approval and external publication are never executed by the graph; the recruiter must use explicit confirmation actions. Publishing intent is resolved by the assistant, then deterministic lifecycle rules and `JobPublishingService` control execution.

The M7.5 product enhancement keeps those boundaries while adding Blogger as a second
`PublisherAdapter` implementation and Gmail/Drive REST adapters. Gmail messages are routed only by
a stable job application code and exact subject, then normalized through the existing application
intake service. Local managed storage remains the canonical CV store; Drive is a non-blocking archive
mirror. Provider transports never own business validation and email body text never enters an AI
workflow.

### F1 Screening Graph
`load persisted M5 profile/chunks -> retrieve relevant CV evidence -> evaluate requirements -> validate evidence -> persist`

### F11 Probe Graph
`load candidate -> identify gaps -> prioritize -> generate probes -> validate -> persist`

### F5 Interview Analysis Graph
`load transcript -> normalize -> segment Q&A -> map requirements -> evaluate -> coverage gaps -> evidence validation -> persist`

### F6 Assessment Graph
`load F1/F2/F5 -> merge evidence -> strengths/weaknesses/gaps -> generate -> claim evidence validation -> persist`

## F2
Final scoring is deterministic Python over validated persisted F1 outcomes. `F2_V1` maps
`MET/PARTIALLY_MET/UNMET` to `100/50/0`, gives required requirements importance `1.0` and preferred
requirements `0.5`, uses positive recruiter-configured relative dimension weights, renormalizes
across applicable dimensions, and persists Decimal results rounded half-up to two places. Match
Rank remains the separate F1 ordinal signal. No LLM, embedding, retrieval, or worker operation runs
during F2 calculation.

## Vector search
- local Ollama `nomic-embed-text` embeddings for M2 company-policy chunks and queries
- M2 policy vectors are 768-dimensional and stored in PostgreSQL + pgvector
- M5 candidate/CV evidence vectors use the same model and 768-dimensional pgvector storage
- PostgreSQL + pgvector cosine similarity
- vector retrieval supplies evidence; it is not the sole decision mechanism

## Company knowledge base
Original PDF, DOCX, and TXT files are stored in a managed Docker volume. PostgreSQL stores document metadata, extracted chunks, vectors, ingestion status, and safe errors. Upload creates a `processing_jobs` record; the worker parses, chunks, embeds, and marks the document `READY` or `FAILED`. SHA-256 prevents duplicate uploads. Deleting a document removes its managed file and cascades its chunks/vectors.

Policy reviews store immutable evidence snapshots, source identifiers, JD version/hash, related JD section, status, and explanation. This preserves historical review traceability after a source document is deleted, without retaining the complete deleted document. A review is current only when its JD version and content hash match the editable JD.

## Application intake
Recruiter batch upload accepts PDF CVs for an existing job. `ApplicationIntakeService` validates
and hashes each file, applies job-scoped duplicate detection, stores it through the minimal
`DocumentStorage` boundary, and persists `Candidate`, `JobApplication`, and `CandidateDocument`
records. `LocalDocumentStorage` writes generated filenames beneath
`/data/applications/<job-code>/`; client filenames never determine physical paths. M4 is
synchronous because it performs only bounded validation, hashing, and local storage. External
Drive/Form/email intake remains deferred.

## Candidate evidence processing
M5 queues each imported candidate document through the existing worker. `CandidateProcessingService`
opens the managed PDF through `DocumentStorage`, extracts page-aware text, persists bounded evidence
chunks, asks Gemini through `LLMAdapter` for a Pydantic-validated profile, validates every profile
evidence reference, embeds chunks through Ollama/Nomic, and stores 768-dimensional vectors in
PostgreSQL. `NEEDS_REVIEW` safely represents scanned/text-poor documents without adding OCR.
Screening, matching, ranking, and scoring remain later milestones.

## F1 candidate screening
M6 queues one `SCREEN_CANDIDATE_APPLICATION` worker task per READY application. The Candidate
Screening Graph loads the persisted M5 profile/chunks, embeds each job requirement as a Nomic query,
retrieves only chunks owned by that application, evaluates all requirements in one structured
Gemini call, validates every requirement/evidence ID deterministically, builds outcome counts, and
persists the screening. Original PDFs are not reopened and candidate chunks are not re-embedded.

Current completed results are ranked in application code by fewer required `UNMET`, fewer required
`PARTIALLY_MET`, more required `MET`, then preferred outcomes and stable application ID. Stale,
queued, processing, and failed screenings are not ranked. F2 weighted scoring remains separate.

## Background processing
`processing_jobs` statuses:
- QUEUED
- RUNNING
- COMPLETED
- FAILED

Each task also stores a 0–100 percentage and safe human-readable stage message. M2 uses `COMPANY_DOCUMENT_INGESTION` and `ASSISTANT_TURN`; M3 adds `JOB_PUBLICATION`; M5 adds `PROCESS_CANDIDATE_DOCUMENT`; M6 adds `SCREEN_CANDIDATE_APPLICATION`. Publication tasks make one provider attempt so an ambiguous external response is never retried automatically.

FastAPI enqueues and returns promptly. Worker processes long-running AI jobs.

## Interview
The persisted `InterviewSession` is the logical interview room. M10A creates no external video
room: a recruiter manually selects a candidate, schedules date/time/timezone/duration, persists the
session, creates a secure candidate-scoped RecrUnion join token/link, and sends the Gmail
invitation. Calendar integration remains optional and deferred.

M10B/M10C historically implemented browser-native WebRTC, FastAPI signalling, and coturn; that
checkpoint remains in Git history. M10-LK supersedes that transport with LiveKit Cloud behind
`VideoAdapter`. FastAPI mints short-lived, exact-room participant tokens after validating the
existing candidate capability or recruiter room request. Opaque provider identities contain no
PII. A lifecycle-sync request asks `LiveKitVideoAdapter` for actual participants: one known human
moves `SCHEDULED` to `WAITING`, both known humans move it to `ACTIVE` and write `started_at` once.
Temporary disconnects do not end or duplicate the session. Interviewer end persists `ENDED` and
`ended_at` before deleting the LiveKit room. Physical cross-network verification remains pending,
so formal F3 is partial rather than complete.

## STT
M11 uses a separately packaged LiveKit publisher agent running as the `transcription-agent` Docker
Compose service. The service connects outbound to LiveKit Cloud and AssemblyAI; it exposes no host
port and is not in the candidate/interviewer media path. LiveKit dispatches one agent job per human
publisher, and the agent subscribes only to that publisher's microphone through provider-neutral
`STTAdapter`. Opaque participant identity maps the result deterministically to `CANDIDATE` or
`INTERVIEWER`, without provider diarization.

Live partial/final events use LiveKit's `lk.transcription` text stream for recruiter display. Only
final normalized segments are sent over an authenticated private Compose callback to `app` and
persisted in PostgreSQL. Public `APP_BASE_URL` remains the browser/invitation origin; the private
callback does not depend on Cloudflare. AssemblyAI schemas remain inside the adapter, and neither
raw audio nor partial text is stored by RecrUnion. The candidate page does not expose the transcript
panel or retrieval API. LiveKit-hosted deployment of the same agent remains a future production
option for managed scaling or geographic placement.

## External actions
- JobDescriptionGraph --`retrieve policy evidence`--> Ollama/Nomic + pgvector
- JobDescriptionGraph --`generate/evaluate JD`--> Gemini
- JobPublishingService --`JobPublicationContent`--> PublisherAdapter --> BlueskyPublisher --> Bluesky
- CandidateProcessingService --`embed CV evidence`--> Ollama/Nomic + pgvector
- ScreeningService --`retrieve persisted CV evidence`--> pgvector
- ProbeGraph --`generate targeted probes`--> Gemini
- InterviewRoomService --> VideoAdapter --> LiveKitVideoAdapter --> LiveKit Cloud
- Docker transcription-agent --> LiveKit Cloud + STTAdapter --> AssemblyAI
- AnalysisGraph --`evaluate Q&A`--> Gemini
- AssessmentGraph --`generate narrative`--> Gemini
- AssessmentService --`generate PDF`--> ReportLab
- NotificationService --`send shortlist/decision email`--> SMTP

## MCP/API
Use a `ToolGateway` abstraction if MCP is introduced.
- direct API adapters remain the reliable baseline
- MCP may expose/consume selected actions
- business logic must not depend directly on transport

Potential MCP tools:
- semantic_evidence_search
- publish_job
- send_candidate_email
- generate_candidate_report

## Evidence-first design
- F1 match -> CV evidence
- F5 evaluation -> transcript segment IDs
- F6 claim -> source IDs

## Deployment
`docker compose up --build`

Use environment variables for provider credentials and configuration.

The existing architecture PNG assets predate ADR-021 and require a later diagram refresh.
