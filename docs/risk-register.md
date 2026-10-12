# Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---:|---:|---|
| R1 | Native WebRTC signalling/ICE/reconnect delay | High | High | Build two-party path incrementally; verify signalling, STUN, then coturn cross-network relay |
| R2 | Hosted streaming STT latency/outage | Medium | High | provider-neutral adapter, partial/final events, visible safe failure, transcript persistence |
| R3 | Nonstandard PDFs | High | Medium | safe failure/NEEDS_REVIEW |
| R4 | Gemini quota/outage | Medium | High | adapter, retries, cache/reuse, visible failure |
| R5 | TURN reachability/credential or relay failure | Medium | High | verify coturn configuration from separate networks before F3 acceptance |
| R6 | Bluesky posting/auth failure | Medium | Medium | adapter + persistent status/error |
| R7 | pgvector dimension mismatch | Medium | Medium | confirm embedding model dimension before migration |
| R8 | MCP complexity | Medium | Medium | direct adapters baseline; MCP only when useful |
| R9 | Over-engineering | High | High | milestone gates; no stretch before core |
| R10 | Docker not clean-machine reproducible | Medium | High | test by Day 19 |
| R11 | PII leak in Git/log/demo | Medium | High | `.gitignore`, sample data, log policy, anonymize |
| R12 | F2 variability | Medium | High | deterministic aggregation/versioned config |
| R13 | F5/F6 unsupported claims | Medium | High | evidence validator blocks unsupported persistence |
| R14 | Interview signalling/STT competes with background work | Medium | Medium/High | keep media out of FastAPI, bound worker concurrency, test active interview plus background processing |
| R15 | Publishing/email extras consume core time | Medium | Medium | thin adapters; F1–F6 priority |
| R16 | User cannot explain generated code | Medium | High | incremental Codex review/explain/commit |
