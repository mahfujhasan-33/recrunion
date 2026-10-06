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
- local SentenceTransformer embeddings
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

- Configure `GEMINI_API_KEY` and optionally `GEMINI_MODEL` in `.env`.
- Generate: `POST /api/v1/jobs/{job_id}/generate-description`
- Save recruiter edits: `PUT /api/v1/jobs/{job_id}/description`
- Explicitly approve: `POST /api/v1/jobs/{job_id}/approve`

Run the local quality checks from a Python 3.12 environment with development dependencies:

```bash
python -m pip install -e ".[dev]"
ruff format --check .
ruff check .
pytest
```

## Privacy
Development and demonstration use provided sample or synthetic candidate data only. The repository must not contain real candidate data, secrets, recordings, transcripts containing PII, or database dumps.
