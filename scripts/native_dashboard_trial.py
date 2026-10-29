"""Explicit host-only camera trial; private DB clone, fixed containers and rollback.

Run as a module from the repository, under a memory/CPU-limited systemd scope.
This is an operator experiment, not a public capability grant or production rollout.
"""

import argparse
import asyncio
import json
import os
import signal
import sqlite3
import time
from contextlib import asynccontextmanager, closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.config import Settings, get_settings
from backend.app.diagnostics.trial_containers import TrialContainers, docker, restore_trial
from backend.app.diagnostics.trial_driver_offer import evidence_reference
from backend.app.diagnostics.trial_motion import motion_grants, write_allowed
from backend.app.diagnostics.trial_sources import prepare_source
from backend.app.diagnostics.trial_storage import TrialStorage
from backend.app.media.config_file import write_private_config
from backend.app.media.server_owner import ServerOwner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera-id")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--restore", type=Path)
    parser.add_argument("--sample-audio", action="store_true")
    parser.add_argument("--native-av", action="store_true", help="test validated native AAC and original-codec archive")
    parser.add_argument("--ptz-direction", action="append", choices=("left", "right", "up", "down"), default=[],
                        help="explicit operator trial scope; permits only target-camera PTZ, no automatic motion")
    parser.add_argument("--recover-at", type=int,
                        help="cleanly retire the test media session at this second, then test automatic recovery")
    parser.add_argument("--driver-av-evidence",type=Path,
                        help="explicit host review document for a normal driver A/V offer; private clone only")
    parser.add_argument("--seconds", type=int, default=900)
    parser.add_argument("--storage-pause", action="store_true",
                        help="pause only the target archive at20s, resume at50s; needs100s and reviewed driver")
    parser.add_argument("--confirm-interruption", action="store_true")
    args = parser.parse_args()
    if args.restore is not None:
        restore_trial(args.restore)
        return
    if (not args.camera_id or not args.directory or not args.confirm_interruption
            or not 30 <= args.seconds <= 900 or (args.sample_audio and args.native_av)
            or (args.ptz_direction and not args.native_av)):
        parser.error("explicit interruption approval and 30..900 seconds required")
    if args.recover_at is not None and (not args.native_av or not 10 <= args.recover_at <= args.seconds-20):
        parser.error("recovery requires native A/V and 20s remaining after retirement")
    if args.storage_pause and (not args.native_av or args.driver_av_evidence is None
            or args.ptz_direction or args.recover_at is not None or args.seconds < 100):
        parser.error("storage trial requires reviewed driver A/V,100s, no motion or session retirement")
    if args.driver_av_evidence is not None:
        if not args.native_av or args.ptz_direction:
            parser.error("reviewed driver trial requires native A/V and no motion")
        evidence_reference(args.driver_av_evidence)
    original = get_settings()
    database = original.db_path.resolve()
    config_path = original.go2rtc_config.resolve()
    containers = TrialContainers()
    # Copy only declared settings, never expose inherited secrets in argv/logs.
    allowed = {key.upper() for key in Settings.model_fields}
    environment = dict(value.split("=", 1) for value in containers.app["Config"]["Env"] if "=" in value)
    os.environ.update({key: value for key, value in environment.items() if key in allowed})
    trial = args.directory
    trial.mkdir(mode=0o700, parents=True, exist_ok=False)
    backup = config_path.read_text()
    write_private_config(trial / "original-go2rtc.json", backup)
    owner = ServerOwner(database)
    clone = trial / "ccg.db"
    server_started = False
    try:
        # Validate encrypted enrollment before interrupting anything.
        get_settings.cache_clear()
        os.environ["DB_PATH"] = str(database)
        get_settings.cache_clear()
        from backend.app.db import p2p, registry
        camera = registry.get_camera_by_id(args.camera_id)
        entry = p2p.read_registered_enrollment(args.camera_id)
        from backend.app import drivers
        if camera is None or entry is None or drivers.for_camera(camera).key != "yoosee":
            raise RuntimeError("reviewed enrolled Yoosee test target required")
        if args.ptz_direction and camera.capabilities.get("ptz") is not True:
            raise RuntimeError("trial target requires existing PTZ support")
        motion_grants(entry, frozenset(args.ptz_direction))
        print(json.dumps({"event": "maintenance_start", "camera_id": camera.camera_id}), flush=True)
        owners = []
        for other in registry.list_cameras():
            if other.camera_id == camera.camera_id and not args.native_av:
                continue
            for moment in (datetime.now(UTC), datetime.now(UTC)+timedelta(hours=1)):
                folder = Path("recordings") / other.camera_id / moment.strftime("%Y-%m-%d/%H")
                if folder.is_dir() and not os.access(folder, os.W_OK):
                    info = folder.stat()
                    owners.append({"path":str(folder.resolve()),"uid":info.st_uid,"gid":info.st_gid})
        manifest = {"app": containers.app["Id"], "hub": containers.hub["Id"], "config": str(config_path),
                    "directory_owners":owners,"helper_image":containers.hub["Image"]}
        write_private_config(trial / "rollback.json", json.dumps(manifest))
        for item in owners:
            docker("run", "--rm", "--network", "none", "--memory", "32m", "--memory-swap", "32m",
                   "--read-only", "--cap-drop", "ALL", "--cap-add", "CHOWN", "--user", "0",
                   "--mount", f"type=bind,src={item['path']},dst=/target", "--entrypoint", "/bin/chown",
                   manifest["helper_image"], f"{os.getuid()}:{os.getgid()}", "/target")
        containers.stop_all()
        # Production owns this same bind-mounted lock until its confirmed exit.
        owner.acquire()
        with (closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as source,
              closing(sqlite3.connect(clone)) as target):
            source.backup(target)
        clone.chmod(0o600)
        os.environ.update(DB_PATH=str(clone.resolve()), AUTOSTART_SERVICES="false", MANAGE_GO2RTC="false",
                          GO2RTC_CONFIG=str(config_path), RECORDINGS_DIR="recordings", FRONTEND_DIR="frontend",
                          CONTROL_SESSION_WARMUP="false")
        get_settings.cache_clear()
        server_started = True
        run_server(args.camera_id, args.seconds, trial, containers, entry,
                   sample_audio=args.sample_audio, native_av=args.native_av,
                   ptz_directions=frozenset(args.ptz_direction), recover_at=args.recover_at,
                   driver_evidence=args.driver_av_evidence, storage_pause=args.storage_pause)
    finally:
        owner.close()  # release the original DB before restarting its app
        if (trial / "rollback.json").exists():
            if not server_started or (trial / "server-stopped.json").exists():
                restore_trial(trial)
            else:
                # ExecStopPost restores after the whole trial cgroup is gone.
                # An uncertain live native owner cannot coexist with production.
                print(json.dumps({"event": "rollback_deferred_until_process_exit"}), flush=True)


def run_server(camera_id, seconds, trial, containers, enrollment, *, sample_audio=False, native_av=False,
               ptz_directions=frozenset(), recover_at=None,driver_evidence=None,storage_pause=False):
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
            planned = await asyncio.to_thread(prepare_source,camera_id,enrollment,recorder,
                sample_audio=sample_audio,native_av=native_av,ptz_directions=ptz_directions,
                recover=recover_at is not None,driver_evidence=driver_evidence)
            audio = planned.audio
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
            status(event="native_ready", camera_id=camera_id, seconds=seconds,
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
    @app.middleware("http")
    async def view_only(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not write_allowed(
                request.url.path, camera_id=camera_id, motion=bool(ptz_directions)):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "view-only native video trial"}, status_code=409)
        return await call_next(request)
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
    server.capture_signals = operator_signals
    try:
        server.run()
    finally:
        lifetime = getattr(app.state, "service_lifetime", None)
        if lifetime is not None and not lifetime.owner.acquired:
            write_private_config(trial / "server-stopped.json", '{"stopped":true}')


if __name__ == "__main__":
    main()
