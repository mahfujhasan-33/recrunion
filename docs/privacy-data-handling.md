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
- **AssemblyAI** — planned for M11 through a LiveKit hosted transcription agent; not yet active
- **Bluesky** — approved public job-post content
- **SMTP provider** — recipient address and notification message

Company-policy embeddings are produced locally through Ollama using `nomic-embed-text`; source
policy documents are not sent to an external embedding service. Relevant retrieved policy excerpts
are sent to Gemini only when needed for JD generation and policy-alignment findings.
PostgreSQL/pgvector and ReportLab remain local. LiveKit processes media only for the active call.
Future M11 transcription will add the separately documented AssemblyAI processing path.

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
