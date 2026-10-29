"""FastAPI application entry point.

Wires the REST API and, on startup, brings up the media + recording stack (unless
``autostart_services`` is off): go2rtc pulls the registry's cameras and restreams them, the
recorder writes 60s segments, and the storage monitor enforces the disk policy. All three
are stopped cleanly on shutdown. Startup is best-effort — if go2rtc's binary is missing the
API still serves so cameras can be managed.

Run with: ``python -m backend.app.main`` (host/port from ``.env``).
Custom Uvicorn launchers must retain ``--no-proxy-headers``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import drivers
from .api.access_keys import router as access_keys_router
from .api.auth import router as auth_router
from .api.cameras import router as cameras_router
from .api.controls import router as controls_router
from .api.discovery import router as discovery_router
from .api.intercom import router as intercom_router
from .api.intercom import stream_router as intercom_stream_router
from .api.media import router as media_router
from .api.native_media import router as native_media_router
from .api.onboarding import router as onboarding_router
from .api.provisioning import router as provisioning_router
from .api.provisioning_account import router as provisioning_account_router
from .api.provisioning_ble import router as provisioning_ble_router
from .api.provisioning_network import router as provisioning_network_router
from .api.provisioning_privileged import router as provisioning_privileged_router
from .api.recordings import router as recordings_router
from .api.settings import router as settings_router
from .api.storage import router as storage_router
from .api.vendor_controls import router as vendor_controls_router
from .config import get_settings
from .control_diagnostics import configure as configure_control_diagnostics
from .db import p2p, registry, source_policy
from .diagnostics.yoosee_av_api import router as native_av_diagnostic_router
from .diagnostics.yoosee_broker_reads_api import router as broker_read_diagnostic_router
from .frontend_build import build_version
from .frontend_static import DashboardFiles
from .media.go2rtc import Go2rtc
from .media.native_runtime import NativeSourceRuntime
from .media.owned_bindings import OwnedIngressBindings
from .media.owned_runtime import OwnedSourceRuntime
from .media.server_owner import ServerOwner
from .media.viewer_gate import ViewerGate
from .recording.playback import Warmer
from .recording.playback_logging import configure as configure_playback_logging
from .recording.recorder import Recorder
from .recording.retention import RetentionCleaner
from .recording.storage import StorageMonitor
from .services.address_recovery import AddressRecovery
from .services.control_sessions import ControlSessions
from .services.lifetime import ServiceLifetime
from .services.native_startup import restore_reviewed
from .validation_errors import invalid_request


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    owner = ServerOwner(settings.db_path)
    owner.acquire()  # before schema/config reconciliation or any service birth
    lifetime = ServiceLifetime(owner)
    app.state.service_lifetime = lifetime  # retain handles even if startup/cleanup fails
    try:
        configure_playback_logging()
        configure_control_diagnostics(settings.db_path.parent / "logs")
        registry.init_db()
        source_policy.init_db()
        p2p.init_db()
        drivers.init_onboarding()

        owned_bindings = OwnedIngressBindings()
        viewers = ViewerGate()
        media = lifetime.own(Go2rtc(owned_ingress=owned_bindings))
        rec = lifetime.own(Recorder(owned_ingress=owned_bindings))
        owned_runtime = lifetime.own(OwnedSourceRuntime(viewers=viewers, recordings=rec.ingress, bindings=owned_bindings))
        native_runtime = lifetime.own(NativeSourceRuntime(viewers=viewers, restart_checkpoints=True))
        storage = lifetime.own(StorageMonitor(rec))
        retention = lifetime.own(RetentionCleaner())
        warmer = lifetime.own(Warmer())
        address_recovery = lifetime.own(AddressRecovery(media, rec))
        control_sessions = lifetime.own(ControlSessions())
        app.state.media = media
        app.state.viewer_sessions = viewers
        app.state.native_runtime = native_runtime
        app.state.native_sources = native_runtime.sources
        app.state.owned_runtime = owned_runtime
        app.state.rec = rec
        app.state.storage = storage
        app.state.retention = retention
        app.state.warmer = warmer
        app.state.address_recovery = address_recovery
        app.state.control_sessions = control_sessions
        app.state.startup_error = None
        app.state.native_startup = {}

        if settings.autostart_services:
            if settings.native_resume_on_start:
                try:
                    await asyncio.to_thread(storage.check)  # Set the disk policy before any native archive birth.
                    def publish_native_routes() -> None:
                        media.write_config()
                    app.state.native_startup = await asyncio.to_thread(restore_reviewed,native_runtime,
                        owned_runtime.retirement,rec,publish_native_routes)
                except Exception:
                    # Broken native state must not prevent the other legacy cameras
                    # from starting. Source policy still excludes native fallback.
                    app.state.native_startup = {"status":"unavailable"}
            try:
                if settings.manage_go2rtc:
                    media.start()
                # The external container does not watch its config. Apply only
                # real changes: unnecessary restart can leave overlapping producers.
                elif not media.ensure_external_config():
                    raise RuntimeError("external go2rtc did not apply its generated configuration")
                if media.wait_healthy(timeout=15):
                    rec.start()
                storage.start()
                retention.start()
                warmer.start()
            except Exception as exc:  # keep API usable after best-effort media startup
                app.state.startup_error = str(exc)
            address_recovery.start()
            if settings.control_session_warmup:
                control_sessions.start()
        yield
    finally:
        # Partial startup is covered too. Keep ASGI runnable for viewer drains.
        await asyncio.to_thread(lifetime.stop)


# Interactive API docs live at /api/docs (Swagger) and /api/redoc; the raw schema at
# /api/openapi.json. A written reference for UI builders is in docs/public/api.md.
API_DESCRIPTION = """
REST API for **Community Cam Guard** — discover, stream, record and control ONVIF/RTSP cameras.

Protected endpoints require a session cookie (`ccg_session`) obtained by logging in
with the dashboard key. Login/logout, session status, build/health and API documentation
are public; public dashboard assets contain no authority to access protected APIs.
See `docs/public/api.md` for the full reference with examples.
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
app.add_exception_handler(RequestValidationError, invalid_request)
app.include_router(native_av_diagnostic_router)
app.include_router(broker_read_diagnostic_router)
app.include_router(auth_router)
app.include_router(access_keys_router)
app.include_router(provisioning_router)
app.include_router(provisioning_account_router)
app.include_router(provisioning_ble_router)
app.include_router(provisioning_network_router)
app.include_router(provisioning_privileged_router)
app.include_router(onboarding_router)
app.include_router(controls_router)
app.include_router(intercom_router)
app.include_router(intercom_stream_router)
app.include_router(vendor_controls_router)
app.include_router(recordings_router)
app.include_router(media_router)
app.include_router(native_media_router)
app.include_router(cameras_router)
app.include_router(discovery_router)
app.include_router(storage_router)
app.include_router(settings_router)


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
    app.mount("/", DashboardFiles(directory=_frontend), name="frontend")


if __name__ == "__main__":
    import uvicorn

    from .access_logging import access_log_config

    s = get_settings()
    # Binds to settings.host (0.0.0.0 by default); dashboard authentication remains mandatory.
    # Keep request.client tied to the transport peer (not attacker-supplied headers).
    # Media payloads are already codec-compressed. Per-message deflate wastes CPU
    # on the NVR and adds no useful video-quality or transport-security benefit.
    uvicorn.run(app, host=s.host, port=s.port, proxy_headers=False,
                ws_per_message_deflate=False, log_config=access_log_config())
