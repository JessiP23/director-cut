# Minimal API image — same ASGI app as Tauri (`app.server:app`).
# Build from repository root so Fly defaults work:
#   docker build -f backend/Dockerfile -t director-cut-api .

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/pyproject.toml .
COPY backend/app ./app

RUN pip install --no-cache-dir .

# Fly and other hosts often set PORT; default keeps local parity with desktop dev.
CMD ["sh", "-c", "exec uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-9420}"]
