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
- **STUN/TURN infrastructure (planned coturn)** — WebRTC connectivity metadata and relayed encrypted interview media only when direct peer connectivity is unavailable
- **AssemblyAI Universal Streaming** — separate candidate/interviewer audio streams for live transcription
- **Bluesky** — approved public job-post content
- **SMTP provider** — recipient address and notification message

Company-policy embeddings are produced locally through Ollama using `nomic-embed-text`; source
policy documents are not sent to an external embedding service. Relevant retrieved policy excerpts
are sent to Gemini only when needed for JD generation and policy-alignment findings.
PostgreSQL/pgvector and ReportLab remain local. Interview audio is no longer entirely local under
ADR-016: it is sent to the configured STT provider only for live transcription.

## Retention and minimization
- raw audio/video recording is disabled by default
- FastAPI does not proxy or persist WebRTC media
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
