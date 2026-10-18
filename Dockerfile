# The reference server (review page, API and webhooks) in one container.
#   docker build -t support-copilot .
#   docker run --rm -p 127.0.0.1:8000:8000 -e API_KEY=change-me -v copilot-data:/data support-copilot
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TRIAGE_DB=/data/triage.db

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY support_copilot ./support_copilot
RUN pip install ".[server]"

# Not root. /data holds the SQLite file (all tickets and decisions): mount a volume there.
RUN useradd --system --uid 10001 --no-create-home copilot \
    && mkdir /data && chown copilot /data
USER copilot
VOLUME /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

# One worker on purpose: the database is one SQLite connection behind a lock (docs/limitations.md).
CMD ["uvicorn", "support_copilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
