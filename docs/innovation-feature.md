# F11 — Smart Interview Probe Generator

## Problem
Generic interviews can waste time confirming already-strong evidence while important uncertainties remain unexplored. CVs often provide partial, indirect, or missing evidence for critical requirements.

## Solution
F11 consumes F1 requirement matches/evidence and F2 score dimensions, identifies weak or missing evidence, prioritizes the most important gaps, and generates 3–5 candidate-specific interview probes.

Each probe contains:
- requirement/area
- priority
- reason
- generated question
- evidence reference or explicit evidence gap
- status (`READY`, `ASKED`, `DISMISSED`)
- flexible expected-evidence guidance, not a canonical perfect answer

## M11.6 implementation and reviewer presentation

F11 is RecrUnion's single defined innovation. The reviewer-facing **Interviewer AI Co-pilot** is
its recruiter-only interview preparation/runtime interface, plus requested grounded follow-ups;
it is not an additional independent innovation. Reviewer labels (including co-pilot/F8 wording)
do not renumber formal BID features.

The graph consumes current saved F1/F2 for the selected application and approved job requirements.
Missing/partial evidence precedes strong evidence; requiredness, configured F2 dimension weight,
source priority and stable IDs resolve ties. When at least three genuine gaps exist, strongly
evidenced requirements are excluded. Otherwise they may support explicit depth-verification probes.
The set contains three to five questions when at least three supported requirements exist; one
or two supported requirements produce only that many questions, with a visible limited-context
notice instead of invented gaps. No eligible context causes a safe prerequisite error.

Probes start READY; the recruiter may mark ASKED or DISMISSED. Those terminal statuses are immutable
apart from idempotent repetition. Regeneration replaces only READY probes and preserves history.
Repeated request IDs and concurrent generation reuse existing tasks; completed unchanged initial
generation is reused until explicit regeneration. Evidence changes require fresh current F1/F2.

Recruiter requests may use the selected interview's latest saved transcript segments to suggest
a follow-up. Transcript-dependent requests need saved segments and cited segment IDs. No F5
evaluation/coverage, live numeric analysis, score mutation, or autonomous hiring advice is produced.
Auth/CSRF and deterministic source/safety validation precede persistence. Pattern-based safety
checks are defense-in-depth, not a proof against every hallucination or prompt injection; human
review remains necessary. Live synthetic acceptance is still required before formal demo closure.

Example:

**Requirement:** Docker  
**Reason:** Required skill; no strong project evidence.  
**Probe:** “Describe a project where you containerised an application with Docker. What did your Dockerfile and deployment workflow look like?”

## Value
Recruiters enter interviews with targeted questions rather than a generic checklist. Candidate interviews become more relevant, and downstream assessment gains better evidence.

## Why prioritized
Alternatives included scheduling, CV-claim contradiction detection, and interview coaching. Probe generation was chosen because it strengthens the mandatory workflow, reuses F1/F2, and avoids sensitive biometric processing or another major integration.

## Integration
`F1 evidence + F2 scores -> identify gaps -> prioritize -> generate probes -> validate -> Interview Preparation -> F3`

## LangGraph
1. load candidate state
2. identify gaps
3. prioritize
4. generate probes
5. validate
6. attach evidence
7. persist

## Acceptance
- 3–5 relevant candidate-specific probes
- reason/evidence per probe
- status can be updated
- feature demonstrated live
- documented in README
- 300–500 word rationale retained in final submission
