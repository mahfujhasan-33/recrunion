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

## Application intake
Candidate applications are collected externally through one recruitment email. No inbound email integration in baseline scope.
Flow:
`candidate emails CV → CV manually saved under data/applications/JOB-xxx → recruiter triggers import → RecrUnion processes PDF`

## Frontend
Final choice: Jinja2 + HTML + CSS + Vanilla JavaScript.
Reason: lower implementation/debugging risk than React while fully supporting required UI behavior.

## Database/vector
PostgreSQL + pgvector. Local SentenceTransformer embeddings persisted for semantic evidence retrieval. No ChromaDB.

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
- exact SentenceTransformer model/dimension
- pgvector/PostgreSQL image tag
