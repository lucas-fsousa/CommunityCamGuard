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
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import drivers
from .api.auth import router as auth_router
from .api.cameras import router as cameras_router
from .api.controls import router as controls_router
from .api.discovery import router as discovery_router
from .api.media import router as media_router
from .api.onboarding import router as onboarding_router
from .api.provisioning import router as provisioning_router
from .api.provisioning_account import router as provisioning_account_router
from .api.provisioning_ble import router as provisioning_ble_router
from .api.provisioning_network import router as provisioning_network_router
from .api.provisioning_privileged import router as provisioning_privileged_router
from .api.recordings import router as recordings_router
from .api.storage import router as storage_router
from .api.vendor_controls import router as vendor_controls_router
from .config import get_settings
from .db import p2p, registry
from .frontend_build import build_version
from .media.go2rtc import Go2rtc
from .recording.playback import Warmer
from .recording.recorder import Recorder
from .recording.retention import RetentionCleaner
from .recording.storage import StorageMonitor


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    registry.init_db()
    p2p.init_db()
    drivers.init_onboarding()

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
                media.start()  # spawn + own the go2rtc binary (host mode)
            else:
                # The external container does not watch its mounted config. Writing without an
                # explicit reload leaves old stream IDs alive after registry/identity migrations;
                # recorders then loop forever on RTSP 404 while the API still looks healthy. Do
                # not restart it when only this app container restarted and config is identical:
                # go2rtc can otherwise retain an old FFmpeg producer beside its replacement.
                if not media.ensure_external_config():
                    raise RuntimeError("external go2rtc did not apply its generated configuration")
            if media.wait_healthy(timeout=15):
                rec.start()
            storage.start()
            retention.start()  # sporadic cleanup of footage past the retention window
            warmer.start()  # opt-in: pre-transcode recent segments for instant playback
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
    version="0.1.0",  # keep in sync with pyproject.toml
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    openapi_tags=[
        {"name": "auth", "description": "Log in/out and check the current session."},
        {"name": "cameras", "description": "List, add, remove, probe and control cameras."},
        {"name": "discovery", "description": "Scan the network for cameras."},
        {
            "name": "provisioning",
            "description": "Factory-new setup; authenticated trusted LAN only.",
        },
        {
            "name": "vendor controls",
            "description": "Typed proprietary controls; authenticated trusted LAN only.",
        },
        {"name": "media", "description": "Live-stream info and media-engine control."},
        {"name": "storage", "description": "Recording storage status."},
        {"name": "recordings", "description": "Browse and fetch recorded segments."},
    ],
)
app.include_router(auth_router)
app.include_router(provisioning_router)
app.include_router(provisioning_account_router)
app.include_router(provisioning_ble_router)
app.include_router(provisioning_network_router)
app.include_router(provisioning_privileged_router)
app.include_router(onboarding_router)
app.include_router(controls_router)
app.include_router(vendor_controls_router)
app.include_router(recordings_router)
app.include_router(media_router)
app.include_router(cameras_router)
app.include_router(discovery_router)
app.include_router(storage_router)


@app.middleware("http")
async def dashboard_cache_policy(request, call_next):
    """Always revalidate the shell/bootstrap; hashed assets may remain cached safely."""
    response = await call_next(request)
    if request.url.path in {"/", "/index.html", "/boot.js", "/api/build"}:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/api/build", include_in_schema=False)
def frontend_build_info() -> JSONResponse:
    settings = get_settings()
    project_root = Path(__file__).resolve().parents[2]
    version = build_version(project_root, settings.frontend_dir)
    return JSONResponse({"version": version}, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Static dashboard (plain HTML/JS, no bundler). Mounted last so /api and /health win.
_frontend = get_settings().frontend_dir
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    # Binds to settings.host (0.0.0.0 by default); dashboard authentication remains mandatory.
    uvicorn.run(app, host=s.host, port=s.port)
