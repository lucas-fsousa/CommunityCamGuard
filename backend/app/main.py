"""FastAPI application entry point.

Wires the REST API and, on startup, brings up the media + recording stack (unless
``autostart_services`` is off): go2rtc pulls the registry's cameras and restreams them, the
recorder writes 60s segments, and the storage monitor enforces the disk policy. All three
are stopped cleanly on shutdown. Startup is best-effort — if go2rtc's binary is missing the
API still serves so cameras can be managed.

Run with: ``uvicorn backend.app.main:app`` (host/port from ``.env``).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import get_settings
from .db import registry
from .media.go2rtc import Go2rtc
from .recording.playback import Warmer
from .recording.recorder import Recorder
from .recording.retention import RetentionCleaner
from .recording.storage import StorageMonitor


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    registry.init_db()

    media = Go2rtc()
    rec = Recorder()
    storage = StorageMonitor(rec)
    retention = RetentionCleaner()
    warmer = Warmer()
    app.state.media = media
    app.state.rec = rec
    app.state.storage = storage
    app.state.retention = retention
    app.state.warmer = warmer
    app.state.startup_error = None

    if settings.autostart_services:
        try:
            if settings.manage_go2rtc:
                media.start()            # spawn + own the go2rtc binary (host mode)
            else:
                media.write_config()     # go2rtc runs elsewhere (container); just feed it
            if media.wait_healthy(timeout=15):
                rec.start()
            storage.start()
            retention.start()            # sporadic cleanup of footage past the retention window
            warmer.start()               # opt-in: pre-transcode recent segments for instant playback
        except Exception as exc:  # missing binary, etc. — keep the API usable
            app.state.startup_error = str(exc)

    try:
        yield
    finally:
        warmer.stop()
        retention.stop()
        storage.stop()
        rec.stop()
        media.stop()


# Interactive API docs live at /api/docs (Swagger) and /api/redoc; the raw schema at
# /api/openapi.json. A written reference for UI builders is in docs/public/api.md.
API_DESCRIPTION = """
REST API for **Community Cam Guard** — discover, stream, record and control ONVIF/RTSP cameras.

All endpoints are under `/api` and, except `POST /api/login`, require a session cookie
(`ccg_session`) obtained by logging in with the dashboard key. Build your own UI against these
endpoints — see `docs/public/api.md` for the full reference with examples.
"""

app = FastAPI(
    title="Community Cam Guard",
    description=API_DESCRIPTION,
    version="0.1.0",   # keep in sync with pyproject.toml
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    openapi_tags=[
        {"name": "auth", "description": "Log in/out and check the current session."},
        {"name": "cameras", "description": "List, add, remove, probe and control cameras."},
        {"name": "discovery", "description": "Scan the network for cameras."},
        {"name": "media", "description": "Live-stream info and media-engine control."},
        {"name": "storage", "description": "Recording storage status."},
        {"name": "recordings", "description": "Browse and fetch recorded segments."},
    ],
)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Static dashboard (plain HTML/JS, no build). Mounted last so /api and /health win.
_frontend = get_settings().frontend_dir
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    # Binds to settings.host (127.0.0.1 by default) — never 0.0.0.0.
    uvicorn.run(app, host=s.host, port=s.port)
