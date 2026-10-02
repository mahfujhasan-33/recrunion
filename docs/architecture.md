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
   Gemini   SentenceTransformer Daily  Bluesky   SMTP
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
`load requirements -> generate -> validate -> persist draft`

Human approval happens outside the graph.

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
- local SentenceTransformer embeddings
- requirement and CV chunk vectors
- PostgreSQL + pgvector cosine similarity
- vector retrieval supplies evidence; it is not the sole decision mechanism

## Background processing
`processing_jobs` statuses:
- QUEUED
- RUNNING
- COMPLETED
- FAILED

FastAPI enqueues and returns promptly. Worker processes long-running AI jobs.

## Interview
Daily.co provides cross-network media transport. RecrUnion persists session, candidate, job, interviewer, status, start/end, transcript, and analysis. Recording is off by default.

## STT
faster-whisper, CPU INT8. Start with `tiny.en`; try `base.en` if latency remains acceptable.

## External actions
- JobService --`generate/refine JD`--> Gemini
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
