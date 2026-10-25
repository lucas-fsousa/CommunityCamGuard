"""Operator closure evidence with mocked Docker; never stop actual containers."""

import json

import pytest

from backend.app.diagnostics import trial_containers as module


def info(name, *, running=True, pid_mode=""):
    return {"Id": name, "Image": "fixed-image", "HostConfig": {"PidMode": pid_mode},
            "Config": {"Labels": {"com.docker.compose.project": "ccg"}},
            "State": {"Running": running, "Restarting": False, "Pid": 123 if running else 0}}


def test_preflight_rejects_shared_pid_namespace_without_stop(monkeypatch):
    calls = []
    def docker(*args):
        calls.append(args)
        return json.dumps([info(args[-1], pid_mode="host")])
    monkeypatch.setattr(module, "docker", docker)
    with pytest.raises(RuntimeError):
        module.TrialContainers()
    assert all(args[0] == "inspect" for args in calls)


@pytest.mark.parametrize("bad", ["running", "image", "pid", "restarting"])
def test_close_requires_exact_identity_and_dead_namespace(monkeypatch, bad):
    monkeypatch.setattr(module, "docker", lambda *args: json.dumps([info(args[-1])]))
    controller = module.TrialContainers()
    current = info("ccg-app", running=False)
    if bad == "running":
        current["State"]["Running"] = True
    elif bad == "image":
        current["Image"] = "changed"
    elif bad == "pid":
        current["State"]["Pid"] = 123
    else:
        current["State"]["Restarting"] = True
    monkeypatch.setattr(controller, "inspect", lambda _: current)
    with pytest.raises(RuntimeError, match="closure unconfirmed"):
        controller.confirm_closed()
