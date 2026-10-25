"""Host-operator Docker boundary for a declared, reversible live trial. No API use."""

import json
import subprocess
from pathlib import Path

from ..media.config_file import write_private_config


def docker(*args: str) -> str:
    try:
        return subprocess.run(["docker", *args], capture_output=True, text=True,
                              timeout=35, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("trial container operation failed") from None


class TrialContainers:
    def __init__(self):
        self.app = self.inspect("ccg-app")
        self.hub = self.inspect("ccg-go2rtc")
        for info in (self.app, self.hub):
            if (not info["State"]["Running"] or info["HostConfig"]["PidMode"] not in ("", "private")
                    or info["Config"]["Labels"].get("com.docker.compose.project") != "ccg"):
                raise RuntimeError("trial requires the running private-PID CCG containers")
        self.changed = False

    @staticmethod
    def inspect(reference: str) -> dict:
        values = json.loads(docker("inspect", reference))
        if len(values) != 1:
            raise RuntimeError("one exact trial container required")
        return values[0]

    def stop_all(self) -> None:
        self.changed = True  # rollback must run even if stop fails midway
        docker("stop", "--time", "20", self.app["Id"])
        docker("stop", "--time", "10", self.hub["Id"])
        self.confirm_closed()

    def confirm_closed(self) -> None:
        for original in (self.app, self.hub):
            current = self.inspect(original["Id"])
            state = current["State"]
            if (current["Id"] != original["Id"] or current["Image"] != original["Image"]
                    or current["HostConfig"]["PidMode"] not in ("", "private")
                    or state["Running"] or state.get("Restarting") or state["Pid"] != 0):
                raise RuntimeError("old container namespace closure unconfirmed")

    def start_hub(self) -> None:
        docker("start", self.hub["Id"])

    def restore(self) -> None:
        docker("start", self.hub["Id"])
        docker("start", self.app["Id"])


def restore_trial(directory: Path) -> None:
    """Also used by systemd ExecStopPost after the trial process/cgroup exits."""
    manifest_path = directory / "rollback.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("directory_owners") and not (directory / "owners-restored.json").exists():
        for item in manifest["directory_owners"]:
            docker("run", "--rm", "--network", "none", "--memory", "32m", "--memory-swap", "32m",
                   "--read-only", "--cap-drop", "ALL", "--cap-add", "CHOWN", "--user", "0",
                   "--mount", f"type=bind,src={item['path']},dst=/target", "--entrypoint", "/bin/chown",
                   manifest["helper_image"], f"{item['uid']}:{item['gid']}", "/target")
        write_private_config(directory / "owners-restored.json", '{"restored":true}')
    if (directory / "restored.json").exists():
        return
    app, hub = (TrialContainers.inspect(manifest[key]) for key in ("app", "hub"))
    path = Path(manifest["config"])
    original = (directory / "original-go2rtc.json").read_text()
    if app["State"]["Running"]:
        if path.read_text() != original:
            raise RuntimeError("unexpected production owner during rollback")
    else:
        docker("stop", "--time", "10", hub["Id"])
        write_private_config(path, original)
        docker("start", hub["Id"])
        docker("start", app["Id"])
    write_private_config(directory / "restored.json", json.dumps({"restored": True}))
    print(json.dumps({"event": "production_restored"}), flush=True)
