# App image (FastAPI + recorder). SKELETON — not used yet; for now run on the host with
# `python -m backend.app.main`. go2rtc runs as its own container (see docker-compose.yml).
FROM python:3.12-slim

# ffmpeg is required by the recorder (segmenting the go2rtc restream).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY frontend ./frontend
RUN pip install --no-cache-dir -e .

# Runs with host networking (see docker-compose.yml) and binds 127.0.0.1:3200 from settings
# — loopback only, nothing exposed to the network. go2rtc runs as its own container, so the
# app is started with MANAGE_GO2RTC=false.
CMD ["python", "-m", "backend.app.main"]
