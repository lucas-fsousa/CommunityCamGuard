"""Explicit host-only camera trial; private DB clone, fixed containers and rollback.

Run as a module from the repository, under a memory/CPU-limited systemd scope.
This is an operator experiment, not a public capability grant or production rollout.
"""

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.config import Settings, get_settings
from backend.app.diagnostics.trial_containers import TrialContainers, docker, restore_trial
from backend.app.diagnostics.trial_driver_offer import evidence_reference
from backend.app.diagnostics.trial_motion import motion_grants
from backend.app.diagnostics.trial_server import run_server
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
    parser.add_argument("--restart-server",action="store_true",
                        help="two clean server lifecycles; second uses ordinary native startup")
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
    if args.restart_server and (not args.native_av or args.driver_av_evidence is None
            or args.ptz_direction or args.recover_at is not None or args.storage_pause):
        parser.error("restart trial requires reviewed driver A/V and no physical/storage/session actions")
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
                          CONTROL_SESSION_WARMUP="false",RECORDING_RETENTION_DAYS="0",
                          PLAYBACK_PRETRANSCODE="false")
        get_settings.cache_clear()
        server_started = True
        run_server(args.camera_id, args.seconds, trial, containers, entry,
                   sample_audio=args.sample_audio, native_av=args.native_av,
                   ptz_directions=frozenset(args.ptz_direction), recover_at=args.recover_at,
                   driver_evidence=args.driver_av_evidence, storage_pause=args.storage_pause)
        if args.restart_server:
            stopped=trial/'server-stopped.json'
            if not stopped.is_file():
                raise RuntimeError('first native server shutdown unconfirmed')
            stopped.replace(trial/'first-server-stopped.json')
            os.environ.update(AUTOSTART_SERVICES='true',NATIVE_RESUME_ON_START='true')
            get_settings.cache_clear()
            run_server(args.camera_id,args.seconds,trial,containers,entry,native_av=True,
                       driver_evidence=args.driver_av_evidence,resume=True)
    finally:
        owner.close()  # release the original DB before restarting its app
        if (trial / "rollback.json").exists():
            if not server_started or (trial / "server-stopped.json").exists():
                restore_trial(trial)
            else:
                # ExecStopPost restores after the whole trial cgroup is gone.
                # An uncertain live native owner cannot coexist with production.
                print(json.dumps({"event": "rollback_deferred_until_process_exit"}), flush=True)



if __name__ == "__main__":
    main()
