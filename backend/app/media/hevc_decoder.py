"""Caller-driven HEVC decoding to null: no display, sound, network or files.

This validates continuous decoding, not browser delivery. Run on a dedicated
consumer, never on the account/socket reader: process teardown can take 1s.
"""

import math
import os
import shutil
import subprocess
import time
from collections.abc import Callable

from .nonblocking_input import NonblockingInput


class HevcDecoder:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        if any(shutil.which(name) is None for name in ("prlimit", "ffmpeg")):
            raise RuntimeError("continuous decoder tools unavailable")
        self._clock = clock
        self._last_time = clock()
        if not math.isfinite(self._last_time):
            raise ValueError("invalid decoder clock")
        self._deadline = self._last_time + 15
        self.frames = 0
        self._buffer = bytearray()
        self._input: NonblockingInput | None = None
        self._closed = False
        command = ["prlimit", "--as=536870912", "--nofile=64", "--",
                   "ffmpeg", "-v", "error", "-nostdin", "-xerror", "-err_detect", "explode",
                   "-filter_threads", "1", "-protocol_whitelist", "pipe", "-threads", "1",
                   "-f", "hevc", "-probesize", "32", "-analyzeduration", "0", "-i", "pipe:0",
                   "-map", "0:v:0", "-an", "-threads", "1", "-progress", "pipe:1",
                   "-stats_period", "0.1", "-f", "null", "-"]
        self._process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, bufsize=0)
        try:
            assert self._process.stdin is not None and self._process.stdout is not None
            # Transfer a duplicate to the writer, closing the Popen stream immediately.
            fd = os.dup(self._process.stdin.fileno())
            self._process.stdin.close()
            self._input = NonblockingInput(fd, clock=clock)
            os.set_blocking(self._process.stdout.fileno(), False)
        except Exception:
            self.close()
            raise

    @property
    def pending_bytes(self) -> int:
        return self._input.pending_bytes if self._input is not None else 0

    def offer(self, payload: bytes) -> bool:
        if self._closed or self._input is None:
            raise ValueError("decoder closed")
        return self._input.offer(payload)

    def step(self) -> None:
        """One output read and one input write; no unbounded drain or frame history."""
        if self._closed:
            raise ValueError("decoder closed")
        try:
            now = self._clock()
            if not math.isfinite(now) or now < self._last_time:
                raise ValueError("invalid decoder clock")
            self._last_time = now
            if self._process.poll() is not None:
                raise RuntimeError("continuous decoder exited")
            assert self._process.stdout is not None and self._input is not None
            try:
                chunk = os.read(self._process.stdout.fileno(), 4096)
            except (BlockingIOError, InterruptedError):
                chunk = b""
            self._buffer.extend(chunk)
            if len(self._buffer) > 8192:
                raise ValueError("decoder progress overflow")
            while b"\n" in self._buffer:
                line, _, rest = self._buffer.partition(b"\n")
                self._buffer = bytearray(rest)
                if line.startswith(b"frame="):
                    value = line[6:].strip()
                    if not value.isdigit() or len(value) > 20:
                        raise ValueError("invalid decoder progress")
                    frames = int(value)
                    if frames < self.frames:
                        raise ValueError("decoder progress regressed")
                    if frames > self.frames:
                        self.frames = frames
                        self._deadline = now + 5
            if now >= self._deadline:
                raise TimeoutError("continuous decoder made no frame progress")
            self._input.pump()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Idempotent retirement; never replace a child with unconfirmed termination."""
        self._closed = True
        self._buffer.clear()
        try:
            if self._input is not None:
                self._input.close()
        finally:
            try:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=0.5)
            finally:
                for stream in (self._process.stdin, self._process.stdout):
                    if stream is not None:
                        stream.close()
