# RecrUnion

**AI-Powered Recruitment & Interview Intelligence Platform**

RecrUnion supports evidence-backed recruitment from job definition and publishing through CV screening, live interview intelligence, and consolidated candidate assessment.

## Core scope
- F1 — CV Screening & Requirement-Based Candidate Ranking
- F2 — AI-Based Candidate Scoring
- F3 — AI-Powered Video Interview
- F4 — Real-Time Speech Recognition & Transcription
- F5 — AI-Based Interview Question & Answer Analysis
- F6 — Consolidated Candidate Assessment
- F11 — Smart Interview Probe Generator

## Technology baseline
- Python 3.12
- FastAPI + Pydantic
- Jinja2 + HTML/CSS/Vanilla JavaScript
- SQLAlchemy
- PostgreSQL + pgvector
- LangGraph
- Gemini through an adapter
- local Ollama `nomic-embed-text` embeddings for company-policy retrieval
- Daily.co
- faster-whisper
- ReportLab
- Docker + Docker Compose

## Documentation
Formal project documentation is under [`/docs`](docs/).

Detailed build/run instructions will be completed as the implementation progresses.

## Quick start

Requirements: Docker with the Compose plugin.

1. Copy `.env.example` to `.env` and change `POSTGRES_PASSWORD` from its placeholder.
2. Start the application:

   ```bash
   docker compose up --build
   ```

3. Open `http://localhost:8000`.

The app container applies pending Alembic migrations before starting FastAPI. To inspect or apply migrations manually:

```bash
docker compose exec app alembic current
docker compose exec app alembic upgrade head
```

Health endpoints:

- `GET /health` checks the web process.
- `GET /ready` checks PostgreSQL connectivity and the pgvector extension.

Job management:

- UI: `GET /jobs`, `/jobs/new`, `/jobs/{job_id}`, `/jobs/{job_id}/edit`
- API: `POST /api/v1/jobs`, `GET /api/v1/jobs`, `GET /api/v1/jobs/{job_id}`, `PUT /api/v1/jobs/{job_id}`

AI job descriptions:

- Configure `GEMINI_API_KEY`, `GEMINI_MODEL`, and the Ollama settings in `.env`.
- Ensure Ollama is running on the host and `nomic-embed-text` is installed: `ollama pull nomic-embed-text`.
- Company documents UI: `GET /company-documents` (PDF, DOCX, and TXT; ingestion runs in the worker).
- Company documents API: `POST/GET /api/v1/company-documents`, `GET/DELETE /api/v1/company-documents/{document_id}`.
- Generate: `POST /api/v1/jobs/{job_id}/generate-description`
- Save recruiter edits: `PUT /api/v1/jobs/{job_id}/description`
- Read/re-run evidence checks: `GET/POST /api/v1/jobs/{job_id}/policy-review`
- Explicitly approve: `POST /api/v1/jobs/{job_id}/approve`

Only relevant retrieved policy excerpts are supplied to Gemini. Approval requires a policy review matching the current JD version and content; editing a generated JD makes the previous review stale until it is rechecked.

Run the local quality checks from a Python 3.12 environment with development dependencies:

```bash
python -m pip install -e ".[dev]"
ruff format --check .
ruff check .
pytest
```

## Privacy
Development and demonstration use provided sample or synthetic candidate data only. The repository must not contain real candidate data, secrets, recordings, transcripts containing PII, or database dumps.
