# RecrUnion Architecture

## Objective
Use the simplest architecture that still satisfies all six mandatory core features, F11, traceability, privacy, and clean Docker execution.

## Runtime
```text
Recruiter / Candidate Browser
            |
            v
FastAPI Web Application
(Jinja2 + HTML/CSS/Vanilla JS)
            |
     +------+----------------------+
     |                             |
     v                             v
Application Services         Daily.co Media
     |
     +--> processing_jobs --> Python Worker
     |                           |
     |                        LangGraph
     |                           |
     +--------------+------------+
                    |
               Adapter Layer
     +--------------+------------------------------+
     |              |            |       |         |
   Gemini      Ollama/Nomic    Daily  Bluesky   SMTP
                    |
              PostgreSQL + pgvector
```

Containers:
1. `app`
2. `worker`
3. `postgres`

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

The assistant is a bounded orchestration layer, not a second implementation of job management. It calls existing services in-process. Requirements, current JD, enhancement proposal, evidence, and required actions are rendered from persisted state in a split-screen Jinja2/Vanilla JavaScript workspace. Approval is never executed by the graph; the recruiter must use the explicit confirmation action.

### F1 Screening Graph
`load -> extract/structure -> embed/retrieve -> evaluate requirements -> validate evidence -> persist`

### F11 Probe Graph
`load candidate -> identify gaps -> prioritize -> generate probes -> validate -> persist`

### F5 Interview Analysis Graph
`load transcript -> normalize -> segment Q&A -> map requirements -> evaluate -> coverage gaps -> evidence validation -> persist`

### F6 Assessment Graph
`load F1/F2/F5 -> merge evidence -> strengths/weaknesses/gaps -> generate -> claim evidence validation -> persist`

## F2
Final scoring is deterministic Python. LLMs may assist with structured evidence interpretation and justifications, not arbitrary final numeric scores.

## Vector search
- local Ollama `nomic-embed-text` embeddings for M2 company-policy chunks and queries
- M2 policy vectors are 768-dimensional and stored in PostgreSQL + pgvector
- candidate/CV vectors remain deferred to the candidate embedding milestone
- PostgreSQL + pgvector cosine similarity
- vector retrieval supplies evidence; it is not the sole decision mechanism

## Company knowledge base
Original PDF, DOCX, and TXT files are stored in a managed Docker volume. PostgreSQL stores document metadata, extracted chunks, vectors, ingestion status, and safe errors. Upload creates a `processing_jobs` record; the worker parses, chunks, embeds, and marks the document `READY` or `FAILED`. SHA-256 prevents duplicate uploads. Deleting a document removes its managed file and cascades its chunks/vectors.

Policy reviews store immutable evidence snapshots, source identifiers, JD version/hash, related JD section, status, and explanation. This preserves historical review traceability after a source document is deleted, without retaining the complete deleted document. A review is current only when its JD version and content hash match the editable JD.

## Background processing
`processing_jobs` statuses:
- QUEUED
- RUNNING
- COMPLETED
- FAILED

Each task also stores a 0–100 percentage and safe human-readable stage message. M2 uses `COMPANY_DOCUMENT_INGESTION` and `ASSISTANT_TURN` job types.

FastAPI enqueues and returns promptly. Worker processes long-running AI jobs.

## Interview
Daily.co provides cross-network media transport. RecrUnion persists session, candidate, job, interviewer, status, start/end, transcript, and analysis. Recording is off by default.

## STT
faster-whisper, CPU INT8. Start with `tiny.en`; try `base.en` if latency remains acceptable.

## External actions
- JobDescriptionGraph --`retrieve policy evidence`--> Ollama/Nomic + pgvector
- JobDescriptionGraph --`generate/evaluate JD`--> Gemini
- PublishingService --`publish approved job`--> Bluesky
- ScreeningService --`embed/search evidence`--> SentenceTransformer + pgvector
- ProbeGraph --`generate targeted probes`--> Gemini
- InterviewService --`create/join room`--> Daily
- TranscriptionService --`transcribe audio`--> faster-whisper
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
