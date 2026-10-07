FROM python:3.12.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv/recrunion

COPY pyproject.toml README.md ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY worker ./worker

RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 recrunion \
    && mkdir -p /data/company-documents \
    && chown -R recrunion:recrunion /data/company-documents

USER recrunion

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
