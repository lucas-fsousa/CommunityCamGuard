"""Isolated Linux subreaper harness. No process-wide changes in pytest/server."""

import ctypes
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from backend.app.media.rtsp_ingress import OwnedRtspIngress

GUARD = Path(__file__).resolve().parents[2] / "backend/app/media/exec_guard.py"


def owner(binary):
    worker = OwnedRtspIngress("cam_" + "d" * 24, "rtsp://127.0.0.1:9/synthetic", binary=binary)

    def announce():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if worker.restream_url:
                print(json.dumps({"pid": worker.pid}), flush=True)
                return
            time.sleep(0.01)
        worker.stop()

    thread = threading.Thread(target=announce, daemon=True)
    thread.start()
    worker.run(cancelled=lambda: False)


def harness(binary):
    # Only this disposable process adopts/reaps the intentional orphan.
    if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise RuntimeError("isolated subreaper unavailable")
    process = subprocess.Popen([
        sys.executable, str(GUARD), str(os.getpid()), sys.executable,
        str(Path(__file__).resolve()), "owner", str(binary),
    ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    child = None
    reaped = False
    try:
        assert process.stdout is not None
        if not select.select([process.stdout], [], [], 6)[0]:
            raise RuntimeError("fixture owner did not become ready")
        child = json.loads(process.stdout.readline(4096))["pid"]
        assert type(child) is int and child > 1
        process.kill()  # deliberate crash of this harness's own Python child
        process.wait(timeout=2)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            pid, status = os.waitpid(child, os.WNOHANG)
            if pid:
                reaped = True
                assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
                print(json.dumps({"worker_killed_on_owner_death": True, "worker_reaped": True}))
                return
            time.sleep(0.01)
        raise RuntimeError("worker survived owner death")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        if child is not None and not reaped:
            # waitpid above establishes this PID is still our unreaped child;
            # never target a reaped PID that the kernel could have reused.
            pid, _status = os.waitpid(child, os.WNOHANG)
            if not pid:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
        # If startup failed before announcing a PID, still reap only descendants
        # adopted by this disposable subreaper. No unrelated process is eligible.
        children = Path(f"/proc/self/task/{threading.get_native_id()}/children")
        for pending in children.read_text().split():
            adopted = int(pending)
            try:
                os.kill(adopted, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(adopted, 0)
        if process.stdout is not None:
            process.stdout.close()


if __name__ == "__main__":
    target = Path(sys.argv[2]).resolve()
    if sys.argv[1] == "owner":
        owner(target)
    else:
        harness(target)
