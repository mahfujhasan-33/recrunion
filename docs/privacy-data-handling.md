# Privacy & Data Handling Note

## Purpose
RecrUnion is a recruitment decision-support platform. Development and demonstration use only provided sample CVs or trainee-generated synthetic data. Real candidate data is not required for this assignment.

## Data processed
The application may process:
- candidate CV text and structured profile information
- interview transcript segments
- candidate scores and evidence-backed assessments
- live audio/video during an interview session

## External services
The current architecture may send limited data to:
- **Gemini API** — selected structured job/CV/transcript text needed for AI reasoning
- **Daily.co** — live interview media transport
- **Bluesky** — approved public job-post content
- **SMTP provider** — recipient address and notification message

Local components such as SentenceTransformer, faster-whisper, PostgreSQL/pgvector, and ReportLab do not require sending candidate data to a separate external AI service.

## Retention and minimization
- raw audio/video recording is disabled by default
- only information required for screening, transcription, analysis, and assessment is persisted
- unnecessary media should not be retained after transcription
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
