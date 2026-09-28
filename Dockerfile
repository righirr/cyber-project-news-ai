# Cyber Security News Powered by AI — container image
# Build and run with: docker compose up -d --build
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Optional Claude SDK: installed in the image so AI enrichment only needs ANTHROPIC_API_KEY.
COPY requirements-ai.txt .
RUN pip install -r requirements-ai.txt

# Unprivileged runtime user for plain `docker run`; compose.yaml instead runs as the owner of
# the host ./data folder (PULSE_UID/PULSE_GID) so the bind-mounted database stays writable.
RUN useradd --system --uid 10001 --no-create-home --home-dir /app pulse \
 && mkdir -p /app/data \
 && chown pulse:pulse /app/data

COPY server.py RELEASE_NOTES.md ./
COPY pulse/ pulse/
COPY static/ static/

USER pulse
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=4)"]

# Inside the container the app listens on every interface; compose decides which host IP publishes it.
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000"]
