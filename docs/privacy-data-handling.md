# Privacy & Data Handling Note

## Purpose
RecrUnion is a recruitment decision-support platform. Development and demonstration use only provided sample CVs or trainee-generated synthetic data. Real candidate data is not required for this assignment.

## Data processed
The application may process:
- internal company hiring-policy documents and extracted evidence chunks
- candidate CV text and structured profile information
- interview transcript segments
- candidate scores and evidence-backed assessments
- live audio/video during an interview session

## External services
The current architecture may send limited data to:
- **Gemini API** — selected structured job/CV/transcript text needed for AI reasoning
- **LiveKit Cloud** — encrypted live audio/video transport using opaque room and participant identifiers; recording and Egress are disabled
- **AssemblyAI** — live microphone audio for M11 transcription through the local Dockerized LiveKit publisher agent
- **Bluesky** — approved public job-post content
- **SMTP provider** — recipient address and notification message

Company-policy embeddings are produced locally through Ollama using `nomic-embed-text`; source
policy documents are not sent to an external embedding service. Relevant retrieved policy excerpts
are sent to Gemini only when needed for JD generation and policy-alignment findings.
PostgreSQL/pgvector and ReportLab remain local. LiveKit processes media only for the active call.
When transcription is enabled, the local agent receives each dispatched microphone track from
LiveKit Cloud, sends it to AssemblyAI, and returns normalized text over the private Compose network;
RecrUnion does not record the media.

## Retention and minimization
- raw audio/video recording is disabled by default
- FastAPI does not proxy or persist LiveKit media
- LiveKit receives only opaque room/participant identifiers; PII mappings remain in RecrUnion
- LiveKit API secrets remain server-side and participant tokens are short-lived and not persisted
- structured transcript segments are persisted; raw STT audio is not stored by default
- deleting a company document removes the managed original file, extracted chunks, and vectors; existing JD reviews retain only the evidence excerpt and source metadata required for historical traceability
- only information required for screening, transcription, analysis, and assessment is persisted
- unnecessary media should not be retained after transcription
- raw audio, audio payloads, API keys, and transcript-provider credentials must not appear in logs
- RecrUnion provides candidate-data deletion for managed records and derived data
- the external/manual recruitment mailbox is outside the application's managed deletion boundary and must be handled operationally

## Access control
Recruiter-only information includes ranking, scoring, assessments, and candidate comparison.

Candidates use an interview-scoped access token/link and must not be able to access recruiter-only information or other candidate records.

M11.5 enforces recruiter authentication for job/application administration, company documents,
assistant conversations/actions, F1/F2 results/configuration, interview management and interviewer
rooms, transcript retrieval, and task status. An initial account is bootstrapped from local
environment configuration; passwords are stored only as salted derivations. Recruiter JWTs remain
in HttpOnly, SameSite cookies, are Secure for HTTPS, and reference server-revocable sessions.
State-changing recruiter requests require a session-bound CSRF token. Login has its own CSRF
check; logout revokes the session. Candidate and internal STT credentials do not authorize these
routes. Responses containing private data or capabilities use `Cache-Control: no-store` and
`Referrer-Policy: same-origin` for recruiter pages so native login/logout forms retain a valid
Origin header without disclosing referrers externally. Candidate capability pages retain
`Referrer-Policy: no-referrer`.

The candidate remains account-less. The STT callback's existing authenticated Bearer secret is
independent of browser login. LiveKit media tokens retain their existing room/participant scope.
Reviewed WBS item 6.1.1 is brought forward before F5/F6; comprehensive M16 security hardening,
including login abuse controls, account recovery, and a deployment security review, is not marked
complete by this foundation.

## Fairness
Protected characteristics such as age, gender, ethnicity, nationality, religion, and marital status must not be used as scoring inputs. AI output is decision support only; the final hiring decision remains human.

## Repository and demonstration hygiene
The submitted repository must not contain:
- real CVs or personal candidate data
- interview recordings/transcripts containing real PII
- database dumps
- API keys or secrets
- screenshots exposing personal candidate information

All demonstration material should be anonymized or synthetic.
