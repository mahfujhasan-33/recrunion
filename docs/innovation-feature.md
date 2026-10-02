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
