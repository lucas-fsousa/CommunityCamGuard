"""Caller-run single-camera RTSP ingress with an owned, RTSP-only go2rtc child.

Not started by lifespan or routes. The coordinator must first retire any legacy
ingress and own admission. No encoder, browser delivery or fallback starts here.
"""

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..camera_identity import valid_camera_id
from .owned_sockets import loopback_listener
from .rtsp_ingress_config import sealed_config, worker_config
from .source_registry import SourceCleanupUnconfirmed, SourceUnavailable

START_TIMEOUT = 5.0
STOP_TIMEOUT = 2.0


class OwnedRtspIngress:
    def __init__(self, camera_id: str, source_url: str, *, binary: Path):
        if type(camera_id) is not str or not valid_camera_id(camera_id):
            raise ValueError("public camera ID required")
        self.camera_id = camera_id
        self._config = worker_config(source_url)
        self._binary = binary.resolve()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._started = False
        self._process: subprocess.Popen | None = None
        self._url: str | None = None

    @property
    def restream_url(self) -> str | None:
        with self._lock:
            return self._url

    @property
    def pid(self) -> int | None:
        with self._lock:
            return self._process.pid if self._process is not None else None

    def stop(self) -> None:
        """Signal only. Await run() completion to confirm physical retirement."""
        self._stop.set()

    def _launch(self) -> subprocess.Popen:
        if sys.platform != "linux" or not self._binary.is_file():
            raise SourceUnavailable("RTSP worker tools unavailable")
        with self._binary.open("rb") as executable:
            if executable.read(4) != b"\x7fELF":
                raise SourceUnavailable("direct RTSP worker executable required")
        fd = sealed_config(self._config)
        try:
            # Exec keeps the same PID; wrapper arms parent death outside the
            # threaded application. No credentials enter argv or child env.
            process = subprocess.Popen([
                sys.executable, "-I", "-S", str(Path(__file__).with_name("exec_guard.py")),
                str(os.getpid()), str(self._binary), "-config", f"/proc/self/fd/{fd}",
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                pass_fds=(fd,), env={"PATH": os.defpath, "LANG": "C", "TZ": "UTC",
                                    "GOMAXPROCS": "1", "GOMEMLIMIT": "64MiB"})
            with self._lock:
                self._process = process
        finally:
            os.close(fd)
        return process

    def _listener(self, process: subprocess.Popen) -> int | None:
        # The bootstrap interpreter is not the producer. Readiness must belong
        # to the actual direct executable under the same unreaped PID.
        try:
            if not self._binary.samefile(Path(f"/proc/{process.pid}/exe")):
                return None
        except FileNotFoundError:
            return None
        return loopback_listener(process.pid)

    def _retire(self) -> None:
        with self._lock:
            self._url = None
            process = self._process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=STOP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            raise SourceCleanupUnconfirmed("RTSP worker exit unconfirmed") from None
        with self._lock:
            self._process = None

    def run(self, *, cancelled: Callable[[], bool]) -> str:
        """Caller owns this thread and quotas; its lifetime must span the child.

        Linux parent death follows the creating thread. Returning normally confirms
        the RTSP-only child was reaped, including all its TCP/UDP sockets. That is
        not a TEARDOWN acknowledgement from firmware or a legacy migration receipt.
        """
        with self._lock:
            if self._started:
                raise ValueError("RTSP ingress already used")
            self._started = True
        try:
            if self._stop.is_set() or cancelled():
                return "stopped"
            process = self._launch()
            deadline = time.monotonic() + START_TIMEOUT
            while not self._stop.is_set() and not cancelled():
                if process.poll() is not None:
                    raise SourceUnavailable("RTSP worker failed before readiness")
                port = self._listener(process)
                if port is not None:
                    with self._lock:
                        self._url = f"rtsp://127.0.0.1:{port}/ingress"
                    break
                if time.monotonic() >= deadline:
                    raise SourceUnavailable("RTSP worker listener unavailable")
                self._stop.wait(0.02)
            while not self._stop.is_set() and not cancelled():
                if process.poll() is not None:
                    return "child_exited"
                self._stop.wait(0.02)
            return "stopped"
        finally:
            self._retire()
