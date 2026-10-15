FROM python:3.12.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /agent
COPY pyproject.toml ./
COPY agent.py ./
COPY recrunion_transcriber ./recrunion_transcriber
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 agent

USER agent
CMD ["python", "agent.py", "start"]
