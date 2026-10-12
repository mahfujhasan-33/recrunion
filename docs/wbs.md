# Work Breakdown Structure — RecrUnion

Planning basis:
- 20 working days / 160h total
- Days 1–3 Phase 1
- Days 4–20 = 17 development days
- core before stretch
- later approved job-generation/publishing requirement included

## M1 Foundation & Phase-1 — 20h
- Clarifications/product/risk register — 4h
- Repo/structure/config baseline — 3h
- FastAPI + PostgreSQL/pgvector foundation — 4h
- Jinja2/HTML/CSS/JS shell — 2h
- Core domain models — 3h
- LangGraph/adapters/architecture docs — 4h

## M2 Job & Pre-Interview — 40h
### Job management/publishing — 8h
- requirements + JD editor/preview — 2h
- JD graph + Gemini + persistent company-policy RAG/evidence — 2h
- approval state + publish guard — 1h
- Bluesky adapter + posting persistence — 3h

### F1 — 22h
- folder import/validation/hash — 4h
- PDF/profile extraction + safe failure — 4h
- chunking/embeddings/pgvector — 4h
- semantic matching + status + evidence — 6h
- ranking + UI — 4h

### F2 — 10h
- configurable weights/deterministic aggregate — 5h
- justifications/persistence/UI — 5h

## M3 During-Interview — 32h
### F3 — 14h
- lifecycle/linkage/join token — 4h
- Daily integration/cross-network — 5h
- controls/persistence/reconnect/end — 5h

### F4 — 18h
- audio/STT CPU setup — 7h
- speaker/timestamp persistence — 5h
- live transcript/retrieval/tuning — 6h

## M4 Post-Interview — 28h
### F5 — 16h
- Q&A segmentation/follow-up — 5h
- evaluation + requirement mapping — 7h
- evidence/coverage/persistence — 4h

### F6 — 12h
- assessment graph + evidence validation — 5h
- report + PDF — 4h
- comparison + traceability UI — 3h

## M5 F11 — 12h
- gap detection/priority — 4h
- probes/reasons/evidence — 4h
- prep UI/status — 4h

## M6 Reliability, Privacy, Delivery — 28h
- recruiter JWT + candidate token — 4h
- validation/safe errors/dependency pinning — 2h
- processing_jobs + worker + progress — 4h
- concurrency/logging — 2h
- deletion/fairness/retention — 4h
- hiring decision + SMTP email — 4h
- Docker/clean machine — 3h
- README/docs — 3h
- benchmark/demo/release — 2h

**Total: 160h**

Suggested sequence:
Days 1–3 M1; Days 4–8 job/F1/F2; Days 9–12 F3/F4; Days 13–14 F5/F6; Days 15–16 F11; Days 17–19 hardening/docs/benchmark; Day 20 final verification/tag/presentation.

Definition of done: working path, relevant failure handling, persistence/access/evidence rules satisfied, repeatable verification, docs updated.
