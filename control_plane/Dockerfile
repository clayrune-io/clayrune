FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/

# Drop root. The service only reads its own code and talks to Firestore/CF over
# the network — it writes nothing to the filesystem — so an unprivileged user
# costs nothing and takes container escape off the table.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Cloud Run sets PORT; default 8080 for local docker run.
ENV PORT=8080

# Cloud Run ignores HEALTHCHECK (it probes the port itself), but this makes
# `docker run` and any plain-Docker deploy report liveness honestly.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/v1/health',timeout=2)" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
