# Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---:|---:|---|
| R1 | Video integration delay | High | High | Prototype Daily early |
| R2 | CPU STT latency | Medium/High | High | `tiny.en` INT8 first; throttle worker during interview |
| R3 | Nonstandard PDFs | High | Medium | safe failure/NEEDS_REVIEW |
| R4 | Gemini quota/outage | Medium | High | adapter, retries, cache/reuse, visible failure |
| R5 | Daily account limitation | Low/Medium | High | verify account before F3 |
| R6 | Bluesky posting/auth failure | Medium | Medium | adapter + persistent status/error |
| R7 | pgvector dimension mismatch | Medium | Medium | confirm embedding model dimension before migration |
| R8 | MCP complexity | Medium | Medium | direct adapters baseline; MCP only when useful |
| R9 | Over-engineering | High | High | milestone gates; no stretch before core |
| R10 | Docker not clean-machine reproducible | Medium | High | test by Day 19 |
| R11 | PII leak in Git/log/demo | Medium | High | `.gitignore`, sample data, log policy, anonymize |
| R12 | F2 variability | Medium | High | deterministic aggregation/versioned config |
| R13 | F5/F6 unsupported claims | Medium | High | evidence validator blocks unsupported persistence |
| R14 | Worker competes with STT CPU | High | Medium/High | worker concurrency=1; throttle/pause during interview |
| R15 | Publishing/email extras consume core time | Medium | Medium | thin adapters; F1–F6 priority |
| R16 | User cannot explain generated code | Medium | High | incremental Codex review/explain/commit |
