"""Linux-only guards for the opt-in, trusted-fixture Chromium harness."""

import os
import signal
import subprocess
import time
from pathlib import Path


def validate_limits(group: Path) -> None:
    try:
        memory = int((group / "memory.max").read_text().strip())
        swap = int((group / "memory.swap.max").read_text().strip())
        tasks = int((group / "pids.max").read_text().strip())
        quota, period = map(int, (group / "cpu.max").read_text().split())
        valid = 0 < memory <= 512 * 1024 * 1024 and swap == 0 and 0 < tasks <= 128 and 0 < quota <= period
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError("Browser test requires explicit cgroup caps: RAM <=512 MiB, swap 0, tasks <=128, CPU <=1 core")


def require_bounded_browser(browser: Path) -> None:
    membership = Path("/proc/self/cgroup").read_text().splitlines()
    unified = next((line.removeprefix("0::") for line in membership if line.startswith("0::")), None)
    if unified is None or ".." in Path(unified).parts:
        raise RuntimeError("Browser test requires a bounded cgroup v2 service")
    validate_limits(Path("/sys/fs/cgroup") / unified.lstrip("/"))
    # Snap launchers can move work outside the caller's cgroup. Use the actual ELF.
    with browser.open("rb") as source:
        if source.read(4) != b"\x7fELF":
            raise RuntimeError("Pass the browser ELF binary directly, not a launcher")


def stop_browser(browser: subprocess.Popen) -> None:
    """Own one new process group; never select unrelated user browsers by name."""
    def kill_group(sig):
        try:
            os.killpg(browser.pid, sig)
        except ProcessLookupError:
            pass
    kill_group(signal.SIGTERM)
    try:
        browser.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    kill_group(signal.SIGKILL)
    browser.wait(timeout=3)
    # Descendants may briefly be exiting after the parent has been reaped. Avoid
    # racing their profile writes against TemporaryDirectory cleanup.
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            os.killpg(browser.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
