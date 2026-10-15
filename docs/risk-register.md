# Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---:|---:|---|
| R1 | LiveKit outage or degraded realtime-media service | Medium | High | safe provider state, scoped credentials, documented provider status and manual acceptance |
| R2 | Streaming STT latency/outage | Medium | High | provider-neutral adapter, publisher-scoped jobs, partial/final live events, visible safe failure, idempotent final persistence, live latency measurement |
| R18 | Local transcription-agent stopped, resource constrained, or disconnected from LiveKit/AssemblyAI | Medium | High | `restart: unless-stopped`, no inbound port, F3/F4 isolation, container/log checks, managed hosting retained as future fallback |
| R3 | Nonstandard PDFs | High | Medium | safe failure/NEEDS_REVIEW |
| R4 | Gemini quota/outage | Medium | High | adapter, retries, cache/reuse, visible failure |
| R5 | LiveKit free-tier quota exhaustion or account configuration failure | Medium | High | monitor quota, fail safely, and verify credentials before demonstrations |
| R6 | Bluesky posting/auth failure | Medium | Medium | adapter + persistent status/error |
| R7 | pgvector dimension mismatch | Medium | Medium | confirm embedding model dimension before migration |
| R8 | MCP complexity | Medium | Medium | direct adapters baseline; MCP only when useful |
| R9 | Over-engineering | High | High | milestone gates; no stretch before core |
| R10 | Docker not clean-machine reproducible | Medium | High | test by Day 19 |
| R11 | PII leak in Git/log/demo | Medium | High | `.gitignore`, sample data, log policy, anonymize |
| R12 | F2 variability | Medium | High | deterministic aggregation/versioned config |
| R13 | F5/F6 unsupported claims | Medium | High | evidence validator blocks unsupported persistence |
| R14 | Interview lifecycle sync or future STT competes with background work | Medium | Medium/High | keep media in LiveKit, use bounded provider calls, and test active interview plus background processing |
| R17 | LiveKit participant token is over-permissioned or leaks | Low/Medium | High | short TTL, exact room/identity, camera/microphone-only publish, backend secret, no token persistence/logging |
| R15 | Publishing/email extras consume core time | Medium | Medium | thin adapters; F1–F6 priority |
| R16 | User cannot explain generated code | Medium | High | incremental Codex review/explain/commit |
