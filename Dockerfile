# App image (FastAPI + recorder). Built and run by docker-compose.yml (the `app` service).
# go2rtc runs as its own container (also in the compose file). To run without Docker instead,
# use `python -m backend.app.main` on the host.
FROM python:3.12-slim

# ffmpeg is required by the recorder (segmenting the go2rtc restream).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg iw libopencore-amrnb0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ARG TARGETARCH
# The owned RTSP boundary needs a direct native binary, not a shell wrapper or
# another container. Verify a pinned architecture-specific artifact once per build.
COPY build_tools/install_go2rtc.py /tmp/install_go2rtc.py
RUN python /tmp/install_go2rtc.py --arch "${TARGETARCH}" --output /app/bin/go2rtc \
    && rm /tmp/install_go2rtc.py
# Documentation/source edits must not invalidate the dependency installation.
COPY pyproject.toml ./
RUN python -c 'import tomllib; data=tomllib.load(open("pyproject.toml", "rb")); print("\n".join(data["project"]["dependencies"] + data["build-system"]["requires"]))' \
        > /tmp/requirements-runtime.txt \
    && pip install --no-cache-dir -r /tmp/requirements-runtime.txt \
    && rm /tmp/requirements-runtime.txt

COPY README.md ./
COPY backend ./backend
COPY frontend ./frontend
RUN pip install --no-cache-dir --no-deps --no-build-isolation .

# Runs with host networking (see docker-compose.yml); the authenticated app defaults
# to LAN 0.0.0.0:3200. Internal go2rtc ports remain loopback-only, and go2rtc runs separately, so the
# app is started with MANAGE_GO2RTC=false.
CMD ["python", "-m", "backend.app.main"]
