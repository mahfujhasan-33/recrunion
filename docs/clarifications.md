# Requirement Clarifications & Decision Log

This file separates BID requirements, later changes, and engineering decisions.

## Timeline interpretation
The BID states 20 working days total and also refers to 17 development days. Project interpretation:
- Days 1–3: Phase 1
- Days 4–20: 17 development days
- total planning basis: 20 working days / 160 hours

## Project Manager reviewer schedule alignment

On 12 October 2026, the Project Manager supplied a reviewer-facing delivery schedule through 27 October. The schedule changes implementation order and review dates only. Formal BID feature numbering and acceptance requirements remain authoritative.

Reviewer-facing dates:

- 5 Oct — Project setup & architecture
- 6 Oct — Job Description Agent
- 7 Oct — Job Posting & CV Collection
- 8 Oct — CV Upload & Screening
- 9 Oct — Semantic Ranking
- 12 Oct — Candidate Scoring
- 13 Oct — Score Persistence & Justification
- 14 Oct — Interview Scheduling & Link
- 15 Oct — Interview UI
- 16 Oct — Video + Transcription
- 19 Oct — Interviewer Co-pilot
- 20 Oct — Q&A Analysis
- 21 Oct — Requirement Mapping
- 22 Oct — Consolidated Assessment
- 23 Oct — Report & Comparison
- 26 Oct — End-to-End Integration & Bug Fixing
- 27 Oct — Final Demo / Reviewer Review

The 19 October reviewer milestone covers the interview, transcription, and formal F11 Smart Interview Probe Generator / ProbeIQ presented as the **Interviewer Co-pilot**. Formal F5 Q&A analysis follows on 20 October, and formal F5 requirement mapping/evidence follows on 21 October. The reviewer labels must not be interpreted as formal SRS feature numbers.

M7.5 multi-channel publishing and automated email intake is a completed enhancement beyond the core BID. It is not F11. Blogger remains an additional publication channel alongside Bluesky, and Google Forms remains deferred.

## Human interview selection and scheduling

Candidate ranking and scoring support recruiter decisions; they do not automatically select a candidate for interview or hire. The required flow is:

`Match Rank + Suitability Score + Evidence → recruiter selects “Select for Interview”`

The Project Manager approved bringing interview scheduling and invitation needs forward into M10A:

`recruiter selects candidate → schedules date/time → creates unique interview session/link → sends invitation → optional calendar event`

A future implementation may use `InterviewSchedulingService` coordinating interview persistence,
`EmailAdapter` (interview invitation), and an optional `CalendarAdapter`. M10A does not provision an
external video room; the persisted `InterviewSession` is the logical room. This clarification does
not implement those components. Final decision communication, rejection notification, and
shortlist notification where applicable remain in M15.

## Approved F3/F4 implementation architecture change

The original F3 implementation baseline was Daily.co. The approved implementation is native
two-party browser WebRTC for exactly one interviewer and one candidate. FastAPI WebSocket handles
SDP offer/answer, ICE candidates, and participant/session presence only. Media remains between the
browser peers, using configured STUN/TURN connectivity with coturn as the TURN server.
FastAPI must not proxy media, and no SFU, group-conferencing platform, or video SaaS is planned.

The original F4 implementation baseline was local faster-whisper CPU transcription. The approved
implementation is AssemblyAI Universal Streaming behind the provider-neutral `STTAdapter`.
Participant browsers send separate transcription audio streams through a session-scoped RecrUnion
STT WebSocket, so authenticated connection context supplies deterministic `CANDIDATE` or
`INTERVIEWER` speaker identity. AssemblyAI diarization is not a core requirement.

The normalized `TranscriptEvent` contract contains interview ID, participant role, start/end
timestamps, text, final/partial state, nullable confidence, and only necessary internal provider
metadata. Interview/session models, browser capture, transcript storage/UI, speaker attribution,
F5, and F6 remain provider-neutral. Raw audio is sent to the configured STT provider only for live
transcription and is not stored by default.

Formal F3 and F4 acceptance behavior is unchanged. The reviewed WBS is not rewritten: its Daily and
CPU-STT lines record the previous implementation assumptions. ADR-016 supersedes those provider
choices while retaining the WBS's lifecycle, cross-network, controls, persistence, reconnect,
speaker/timestamp, and live-transcript work.

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

## Candidate CV processing
M5 processes imported CVs asynchronously through the existing PostgreSQL worker. Text is
extracted page-by-page, divided into page-linked evidence chunks, structured through Gemini behind
`LLMAdapter`, and embedded locally through the existing Ollama/Nomic `EmbeddingAdapter`. Profiles
may contain contact details, education, work history, skills, certifications, projects, and tools,
but every item must reference a persisted chunk and protected characteristics are excluded.
Text-poor/scanned documents become `NEEDS_REVIEW`; OCR is deferred.

## F1 candidate screening and ranking
M6 consumes only persisted M5 profiles and candidate CV chunks. Each current job requirement is
matched against application-scoped pgvector evidence, evaluated through Gemini behind
`LLMAdapter`, and deterministically validated before persistence. Results use `MET`,
`PARTIALLY_MET`, and `UNMET`; `UNMET` means the submitted CV does not sufficiently demonstrate the
requirement, not that the candidate lacks the capability. Ranking is a reproducible ordinal F1
ordering based on required outcomes first and preferred outcomes second. It is not the configurable
weighted F2 score. Protected-characteristic requirements fail safely and never participate.

## Frontend
Final choice: Jinja2 + HTML + CSS + Vanilla JavaScript.
Reason: lower implementation/debugging risk than React while fully supporting required UI behavior.

## Database/vector
PostgreSQL + pgvector. Local embeddings persisted for semantic evidence retrieval. M2 company-policy retrieval and M5 candidate-CV evidence use the locally hosted Ollama `nomic-embed-text` model with 768-dimensional vectors. No ChromaDB.

## Approved scope change — persistent company knowledge base
M2 job-description generation must retrieve only relevant evidence from reusable company hiring documents. Recruiters can upload, list, inspect, and remove PDF, DOCX, or TXT documents. Parsing, chunking, and local embedding run through the PostgreSQL-backed worker. Generated descriptions include evidence-backed policy findings (`MET`, `PARTIALLY_MET`, `NOT_MET`, or `NOT_APPLICABLE`) rather than an opaque compliance score. Human approval remains authoritative, and recruiter edits require a fresh policy check before approval.

For a generated JD, recruiters may re-run retrieval at any time so documents that became ready later are considered. When the current review contains `PARTIALLY_MET` or `NOT_MET` findings, an evidence-driven enhancement may revise the narrative, create a new JD version, and immediately produce a fresh review. Recruiter-entered mandatory requirements remain authoritative and approval remains a separate human action.

## Approved scope change — recruiter assistant workspace
The M1/M2 workflow is also available through one persistent recruiter-assistant conversation and a split-screen active workspace. The assistant may suggest a structured requirement draft, but recruiter edits remain authoritative. Chat intents are restricted to typed actions that reuse `JobService`, the JD graphs, `PolicyReviewService`, and approval rules; the model cannot call providers or mutate persistence directly. Assistant turns run through the PostgreSQL worker and expose percentage plus a human-readable stage message. JD enhancement requested in chat is shown as a proposal before it can replace the current editable JD, and approval always requires an explicit recruiter confirmation.

## LLM
Gemini behind `LLMAdapter`. Use only sample/synthetic candidate data.

## Video
Browser-native WebRTC for one interviewer and one candidate. FastAPI WebSocket provides signalling
only; STUN/TURN with coturn supports cross-network connectivity. RecrUnion owns interview
session/auth/status/transcript/analysis. Raw recording is off by default.

## STT
AssemblyAI Universal Streaming behind provider-neutral `STTAdapter`. Separate participant streams
provide speaker identity. RecrUnion persists normalized transcript segments, not raw audio.

## Background work
Python worker + PostgreSQL `processing_jobs`. No Redis/Celery.

## Authentication
Recruiter: pre-created username/password + JWT. No public signup.
Candidate: interview-scoped secure join token; no registration.

## Notifications
Outbound shortlist/final-decision notification through `EmailAdapter` with SMTP baseline.
Inbound application email was pulled forward as an approved M7.5 product enhancement beyond core
BID. It uses exact-subject Gmail REST discovery, accepts one PDF attachment, and feeds the existing
application intake pipeline. Google Forms remains outside scope.

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
- WebRTC STUN/TURN endpoints and coturn credentials for cross-network verification
- AssemblyAI API key and Universal Streaming availability/limits
- Bluesky demo account/app password
- SMTP account
- candidate/CV retrieval tuning (M2 policy and M5 CV vectors use `nomic-embed-text`, 768 dimensions)
- pgvector/PostgreSQL image tag
