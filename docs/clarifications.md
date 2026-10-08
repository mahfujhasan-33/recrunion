# Requirement Clarifications & Decision Log

This file separates BID requirements, later changes, and engineering decisions.

## Timeline interpretation
The BID states 20 working days total and also refers to 17 development days. Project interpretation:
- Days 1–3: Phase 1
- Days 4–20: 17 development days
- total planning basis: 20 working days / 160 hours

## WBS AI-use exception
The BID says WBS/SRS/workflow diagram should be completed without AI support. The project owner/user has stated that PM permission was later granted to use AI for the WBS. Retain the approval evidence if available.

## Approved scope change — job generation & publishing
The original BID lists job-board publishing as out of scope. Later requirement:
1. recruiter defines requirements
2. AI generates JD
3. recruiter reviews/edits/regenerates
4. recruiter explicitly approves
5. only approved job can publish
6. Bluesky is development/demo provider
7. provider abstraction required

Lifecycle:
`DRAFT → GENERATED → APPROVED → PUBLISHING → PUBLISHED`
Failure:
`PUBLISH_FAILED`

M3 publishing runs as a PostgreSQL-backed worker task. The normal job page and the
recruiter assistant both call the same deterministic publishing service. A chat request
only proposes the typed publication action; the recruiter must explicitly confirm before
the approved job is sent to Bluesky. Failed attempts require an explicit retry, while
`PUBLISHING` and `PUBLISHED` reject duplicate publication.

## Application intake
M4 provides direct recruiter batch upload of PDF CVs from the job UI. RecrUnion validates,
hashes, stores, and records each file independently. Managed files use generated names under
the stable job code; original filenames remain metadata only. Inbound email, Google Drive,
and Google Forms integrations remain outside M4.

Flow:
`recruiter selects PDF CVs → batch upload → validate/hash/deduplicate → managed local storage → application records`

## Frontend
Final choice: Jinja2 + HTML + CSS + Vanilla JavaScript.
Reason: lower implementation/debugging risk than React while fully supporting required UI behavior.

## Database/vector
PostgreSQL + pgvector. Local embeddings persisted for semantic evidence retrieval. M2 company-policy retrieval uses the locally hosted Ollama `nomic-embed-text` model with 768-dimensional vectors. Candidate/CV embedding choices remain part of the later candidate milestone. No ChromaDB.

## Approved scope change — persistent company knowledge base
M2 job-description generation must retrieve only relevant evidence from reusable company hiring documents. Recruiters can upload, list, inspect, and remove PDF, DOCX, or TXT documents. Parsing, chunking, and local embedding run through the PostgreSQL-backed worker. Generated descriptions include evidence-backed policy findings (`MET`, `PARTIALLY_MET`, `NOT_MET`, or `NOT_APPLICABLE`) rather than an opaque compliance score. Human approval remains authoritative, and recruiter edits require a fresh policy check before approval.

For a generated JD, recruiters may re-run retrieval at any time so documents that became ready later are considered. When the current review contains `PARTIALLY_MET` or `NOT_MET` findings, an evidence-driven enhancement may revise the narrative, create a new JD version, and immediately produce a fresh review. Recruiter-entered mandatory requirements remain authoritative and approval remains a separate human action.

## Approved scope change — recruiter assistant workspace
The M1/M2 workflow is also available through one persistent recruiter-assistant conversation and a split-screen active workspace. The assistant may suggest a structured requirement draft, but recruiter edits remain authoritative. Chat intents are restricted to typed actions that reuse `JobService`, the JD graphs, `PolicyReviewService`, and approval rules; the model cannot call providers or mutate persistence directly. Assistant turns run through the PostgreSQL worker and expose percentage plus a human-readable stage message. JD enhancement requested in chat is shown as a proposal before it can replace the current editable JD, and approval always requires an explicit recruiter confirmation.

## LLM
Gemini behind `LLMAdapter`. Use only sample/synthetic candidate data.

## Video
Daily.co for reliable cross-network browser video/audio. RecrUnion owns interview session/auth/status/transcript/analysis. Recording off by default.

## STT
Local faster-whisper, CPU INT8. Start with `tiny.en` or `base.en`; benchmark.

## Background work
Python worker + PostgreSQL `processing_jobs`. No Redis/Celery.

## Authentication
Recruiter: pre-created username/password + JWT. No public signup.
Candidate: interview-scoped secure join token; no registration.

## Notifications
Outbound shortlist/final-decision notification through `EmailAdapter` with SMTP baseline.
Inbound application email remains outside integration scope.

## MCP/API
User intends to use MCP/API tool integration.
Interpretation:
- services depend on adapters/interfaces
- direct APIs are baseline reliable implementation
- selected capabilities may be exposed/consumed via MCP `ToolGateway`
- MCP must not be required for every external call

## Stretch
F7–F10 remain optional and must wait until F1–F6 + F11 are working.

## Confirm at kickoff
- exact Gemini model/quota
- Daily account/domain/API key
- Bluesky demo account/app password
- SMTP account
- candidate/CV embedding model/dimension (M2 company-policy vectors are fixed to `nomic-embed-text`, 768 dimensions)
- pgvector/PostgreSQL image tag
