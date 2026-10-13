import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from backend.app.drivers.yoosee.p2p import list_dns as dns
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError


class Pipe:
    closed = False
    def close(self): self.closed = True


class Child:
    def __init__(self, output=b'["192.0.2.1"]', running=False):
        self.output = output
        self.running = running
        self.returncode = None if running else 0
        self.stdout = Pipe()
        self.killed = self.waited = False

    def communicate(self, timeout):
        if self.running: raise subprocess.TimeoutExpired("redacted", timeout)
        return self.output, None

    def poll(self): return self.returncode
    def kill(self):
        self.killed = True
        self.returncode = -9
    def wait(self, timeout): self.waited = True


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(dns.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(dns, "_blocked", False)
    return now


def test_small_child_no_app_environment_or_shell(monkeypatch, clock):
    child = Child(b'["192.0.2.1", "192.0.2.1"]')
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "private")
    def spawn(args, **kwargs):
        assert args[:3] == [sys.executable, "-I", "-S"]
        assert set(kwargs["env"]) <= {"SYSTEMROOT"}
        assert "shell" not in kwargs and kwargs["close_fds"]
        assert kwargs["stderr"] == kwargs["stdin"] == subprocess.DEVNULL
        return child
    monkeypatch.setattr(dns.subprocess, "Popen", spawn)
    assert dns.resolve_list_hosts(20) == ("192.0.2.1",)
    assert child.waited and child.stdout.closed and not child.killed


@pytest.mark.parametrize("deadline,cancelled", [(0, False), (20, True)])
def test_pre_cancel_or_expiry_never_spawns(monkeypatch, clock, deadline, cancelled):
    monkeypatch.setattr(dns.subprocess, "Popen", lambda *a, **k: pytest.fail("spawn"))
    with pytest.raises(P2PProbeError):
        dns.resolve_list_hosts(deadline, cancelled=lambda: cancelled)


@pytest.mark.parametrize("deadline,end", [(20, 5.0), (0.2, 0.2)])
def test_timeout_kills_and_reaps_before_next_lookup(monkeypatch, clock, deadline, end):
    child = Child(running=True)
    def communicate(timeout):
        assert 0 < timeout <= 0.05
        clock[0] += timeout
        raise subprocess.TimeoutExpired("private", timeout)
    monkeypatch.setattr(child, "communicate", communicate)
    monkeypatch.setattr(dns.subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(P2PProbeError, match="deadline"):
        dns.resolve_list_hosts(deadline)
    assert end <= clock[0] < end + 0.051
    assert child.killed and child.waited and child.stdout.closed
    assert not dns._blocked


def test_cancel_during_wait_and_no_competing_child(monkeypatch, clock):
    child = Child(running=True)
    cancel = [False]
    def communicate(timeout):
        with pytest.raises(P2PProbeError, match="busy"):
            dns.resolve_list_hosts(20)
        cancel[0] = True
        raise subprocess.TimeoutExpired("private", timeout)
    monkeypatch.setattr(child, "communicate", communicate)
    monkeypatch.setattr(dns.subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(P2PProbeError, match="cancellation"):
        dns.resolve_list_hosts(20, cancelled=lambda: cancel[0])
    assert child.killed and child.waited


def test_unconfirmed_exit_blocks_replacement(monkeypatch, clock):
    child = Child()
    def wait(timeout): raise subprocess.TimeoutExpired("private", timeout)
    monkeypatch.setattr(child, "wait", wait)
    monkeypatch.setattr(dns.subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(P2PProbeError, match="cleanup"):
        dns.resolve_list_hosts(20)
    monkeypatch.setattr(dns.subprocess, "Popen", lambda *a, **k: pytest.fail("leak"))
    with pytest.raises(P2PProbeError, match="cleanup"):
        dns.resolve_list_hosts(20)


@pytest.mark.parametrize("output", [b'{}', b'[]', b'null', b'bad', b'\xff',
    b'["hostname.invalid"]', b'["::1"]', b'[123]', b'x' * 1025,
    json.dumps(["192.0.2.1"] * 17).encode()])
def test_invalid_results_fail_closed(monkeypatch, clock, output):
    child = Child(output)
    monkeypatch.setattr(dns.subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(P2PProbeError): dns.resolve_list_hosts(20)
    assert child.waited and child.stdout.closed


def test_deadline_after_spawn_rejects_even_success(monkeypatch, clock):
    child = Child()
    def spawn(*args, **kwargs):
        clock[0] = 6.0
        return child
    monkeypatch.setattr(dns.subprocess, "Popen", spawn)
    with pytest.raises(P2PProbeError, match="deadline"): dns.resolve_list_hosts(20)
    assert child.waited


def test_process_creation_failure_releases_gate(monkeypatch, clock):
    def spawn(*args, **kwargs): raise OSError("private")
    monkeypatch.setattr(dns.subprocess, "Popen", spawn)
    with pytest.raises(P2PProbeError, match="unavailable"): dns.resolve_list_hosts(20)
    assert not dns._blocked
    assert dns._gate.acquire(blocking=False)
    dns._gate.release()


def test_real_isolated_child_numeric_address_without_network_lookup():
    result = subprocess.run([sys.executable, "-I", "-S",
        str(Path(dns.__file__).with_name("list_dns_child.py")), "192.0.2.1", "51701"],
        capture_output=True, timeout=3, check=True)
    assert json.loads(result.stdout) == ["192.0.2.1"]
    assert result.stderr == b""


def test_real_resolver_wrapper_numeric_host(monkeypatch):
    monkeypatch.setattr(dns, "LIST_HOST", "192.0.2.1")
    assert dns.resolve_list_hosts(time.monotonic() + 3) == ("192.0.2.1",)


def test_real_hung_child_is_killed_and_reaped(monkeypatch):
    spawn, children = subprocess.Popen, []
    def sleeping_child(args, **kwargs):
        child = spawn([sys.executable, "-I", "-S", "-c", "import time; time.sleep(60)"], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(dns.subprocess, "Popen", sleeping_child)
    with pytest.raises(P2PProbeError, match="deadline"):
        dns.resolve_list_hosts(time.monotonic() + 0.15)
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdout.closed and not dns._blocked


def test_interrupt_during_wait_still_reaps(monkeypatch, clock):
    child = Child(running=True)
    def communicate(timeout): raise KeyboardInterrupt
    monkeypatch.setattr(child, "communicate", communicate)
    monkeypatch.setattr(dns.subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(KeyboardInterrupt): dns.resolve_list_hosts(20)
    assert child.killed and child.waited and child.stdout.closed


def test_interrupt_during_cleanup_blocks_replacement(monkeypatch, clock):
    child = Child()
    def wait(timeout): raise KeyboardInterrupt
    monkeypatch.setattr(child, "wait", wait)
    monkeypatch.setattr(dns.subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(KeyboardInterrupt): dns.resolve_list_hosts(20)
    assert dns._blocked and child.stdout.closed
    monkeypatch.setattr(dns.subprocess, "Popen", lambda *a, **k: pytest.fail("replacement"))
    with pytest.raises(P2PProbeError, match="cleanup"): dns.resolve_list_hosts(20)
