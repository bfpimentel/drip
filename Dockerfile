FROM docker.io/library/python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app.py ./
COPY templates/ ./templates/
COPY static/ ./static/

RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin drip \
    && mkdir -p /app/uploads /app/data \
    && chown drip:drip /app/uploads /app/data

USER drip

EXPOSE 7123

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '7123')}/healthz\", timeout=3)"]

CMD ["/app/.venv/bin/python", "app.py"]
