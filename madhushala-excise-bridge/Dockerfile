FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fluxbox \
        fonts-liberation \
        novnc \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY app ./app
COPY docker/entrypoint.sh /app/docker/entrypoint.sh

RUN mkdir -p /app/data/browser_profile /app/data/captures /app/data/mappings \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && chmod +x /app/docker/entrypoint.sh \
    && chown -R appuser:appuser /app /ms-playwright

USER appuser

EXPOSE 8091 6080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${LOCAL_PORT:-8091}/health || exit 1

CMD ["/app/docker/entrypoint.sh"]
