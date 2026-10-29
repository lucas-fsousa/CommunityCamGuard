"""Bounded operator server lifecycle, separate from Docker/clone orchestration."""

import asyncio
import json
import signal
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from ..config import get_settings
from ..media.config_file import write_private_config
from .trial_motion import write_allowed
from .trial_sources import TrialSource, prepare_source
from .trial_storage import TrialStorage


def run_server(camera_id, seconds, trial, containers, enrollment, *, sample_audio=False, native_av=False,
               ptz_directions=frozenset(), recover_at=None,driver_evidence=None,storage_pause=False,resume=False):
    import uvicorn

    from backend.app.main import app

    ordinary = app.router.lifespan_context
    output = trial / "status.json"

    def status(**values):
        write_private_config(output, json.dumps(values))
        print(json.dumps(values), flush=True)

    @asynccontextmanager
    async def lifespan(application):
        async with ordinary(application):
            runtime = application.state.native_runtime
            retirement = application.state.owned_runtime.retirement
            storage = TrialStorage(application.state.rec) if storage_pause else None
            recorder = storage.recorder if storage is not None else application.state.rec
            if resume:
                activation=runtime._activations.get(camera_id)
                session=activation.session if activation else None
                if (application.state.native_startup.get(camera_id)!='running'
                        or session is None or session.source is None):
                    status(event='native_restart_failed',statuses=application.state.native_startup)
                    raise RuntimeError('ordinary native restart was not admitted')
                planned=TrialSource(session.offer,session.source)
            else:
                planned = await asyncio.to_thread(prepare_source,camera_id,enrollment,recorder,
                    sample_audio=sample_audio,native_av=native_av,ptz_directions=ptz_directions,
                    recover=recover_at is not None,driver_evidence=driver_evidence)
                receipt = await asyncio.to_thread(retirement.retire_legacy, camera_id,
                    expected_revision=0, retire_ingress=containers.confirm_closed)
                try:
                    session = await asyncio.to_thread(runtime.activate, retirement=retirement,
                        receipt=receipt, offer=planned.offer, ready_timeout=30,
                        apply_routes=lambda: application.state.media.write_config())
                except Exception as error:
                    activation = runtime._activations.get(camera_id)
                    prepared = activation.session if activation else None
                    result = prepared.completion if prepared else None
                    reason = "unknown"
                    if result is not None and result.done() and result.exception() is None:
                        reason = getattr(result.result(), "reason", "unknown")
                    status(event="native_start_failed", error_type=type(error).__name__, reason=reason,
                           **planned.diagnostics())
                    raise
                await asyncio.to_thread(containers.start_hub)
                if not await asyncio.to_thread(application.state.media.wait_healthy, timeout=15):
                    raise RuntimeError("other-camera hub did not return")
                await asyncio.to_thread(application.state.rec.start)
            audio = planned.audio
            assert session is not None
            status(event="native_ready", camera_id=camera_id, seconds=seconds,
                   boot='restored' if resume else 'initial',
                   other_recorders=len(application.state.rec._procs), frame_rate=planned.frame_rate,
                   source_origin="driver" if driver_evidence is not None else "explicit-trial",
                   archive_segment_seconds=(getattr(planned.current,"_archive_segment_seconds",None)
                                            if native_av else None),
                   ptz_directions=sorted(ptz_directions))
            async def monitor():
                started = time.monotonic()
                sampled = False
                retired = False
                while time.monotonic() - started < seconds:
                    await asyncio.sleep(5 if storage is not None else 10)
                    if storage is not None:
                        changed = storage.advance(time.monotonic()-started)
                        if changed is not None:
                            status(event="native_storage_policy", policy=changed,
                                   elapsed=round(time.monotonic()-started))
                    if recover_at is not None and not retired and time.monotonic()-started >= recover_at:
                        if planned.retire_session():
                            retired = True
                            status(event="native_retirement_requested", elapsed=round(time.monotonic()-started))
                    if audio is not None and not sampled:
                        metadata = await asyncio.to_thread(audio.save, trial)
                        status(event="native_audio_sample", **metadata)
                        sampled = True
                    if session.completion.done():
                        try:
                            result = session.completion.result()
                            status(event="native_ended", elapsed=round(time.monotonic()-started),
                                   reason=result.reason, session_reason=result.session.reason,
                                   consumer_error=result.failure_type,
                                   consumer_site=planned.diagnostics().get("consumer_site",""),
                                   mp4_expected=planned.diagnostics().get("consumer_mp4_expected",""),
                                   mp4_actual=planned.diagnostics().get("consumer_mp4_actual",""),
                                   audio_error=planned.diagnostics().get("audio_error",""))
                        except Exception as error:
                            status(event="native_ended", error_type=type(error).__name__,
                                   **planned.diagnostics())
                        break
                    status(event="native_running", elapsed=round(time.monotonic()-started),
                           reservations=list(runtime.sources.states().values()),
                           other_recorders=len(application.state.rec._procs),
                           frame_rate=planned.frame_rate,restarts=planned.restarts,
                           saving_paused=storage.paused if storage is not None else False,
                           native_recording_allowed=getattr(planned.current,"_recording_allowed",None),
                           descriptors=len(list(Path('/proc/self/fd').iterdir())))
                server.should_exit = True
            task = asyncio.create_task(monitor())
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app.router.lifespan_context = lifespan
    # View-only by default; explicit trial motion never grants light/sound/other writes.
    async def view_only(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not write_allowed(
                request.url.path, camera_id=camera_id, motion=bool(ptz_directions)):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "view-only native video trial"}, status_code=409)
        return await call_next(request)
    if not resume:
        app.middleware("http")(view_only)
    server = uvicorn.Server(uvicorn.Config(app, host=get_settings().host, port=get_settings().port,
                            proxy_headers=False, ws_per_message_deflate=False,
                            timeout_graceful_shutdown=30, log_level="warning"))
    @contextmanager
    def operator_signals():
        # Do not re-raise SIGTERM before the outer rollback finally runs.
        before = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        for sig in before:
            signal.signal(sig, lambda *_: setattr(server, "should_exit", True))
        try:
            yield
        finally:
            for sig, handler in before.items():
                signal.signal(sig, handler)
    server.capture_signals = operator_signals  # type: ignore[method-assign]  # Owned rollback before signal propagation.
    try:
        server.run()
    finally:
        app.router.lifespan_context = ordinary
        lifetime = getattr(app.state, "service_lifetime", None)
        if lifetime is not None and not lifetime.owner.acquired:
            write_private_config(trial / "server-stopped.json", '{"stopped":true}')


