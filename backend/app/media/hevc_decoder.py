"""Caller-driven HEVC decoding or H.264/fMP4 output; no display or audio.

No public route is provided. Run on a dedicated
consumer, never on the account/socket reader: process teardown can take 1s.
"""

import math
import os
import shutil
import subprocess
import time
from collections.abc import Callable

from .fragmented_mp4 import FragmentedMp4
from .nonblocking_input import NonblockingInput


class HevcDecoder:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 output: FragmentedMp4 | None = None):
        if output is not None and (output.closed or output.started):
            raise ValueError("fresh MP4 output required")
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
        self._output = output
        command = ["prlimit", "--as=536870912", "--nofile=64", "--",
                   "ffmpeg", "-v", "error", "-nostdin", "-xerror", "-err_detect", "explode",
                   "-filter_threads", "1", "-protocol_whitelist", "pipe", "-threads", "1",
                   "-f", "hevc", "-probesize", "32", "-analyzeduration", "0", "-i", "pipe:0",
                   "-map", "0:v:0", "-an", "-threads", "1"]
        if output is None:
            command += ["-progress", "pipe:1", "-stats_period", "0.1", "-f", "null", "-"]
        else:
            # Keep media stdout separate from machine-readable progress on stderr.
            command += ["-v", "quiet", "-progress", "pipe:2", "-stats_period", "0.1",
                        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                        "-pix_fmt", "yuv420p", "-profile:v", "baseline", "-g", "25",
                        "-keyint_min", "25", "-sc_threshold", "0",
                        "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
                        "-f", "mp4", "pipe:1"]
        if output is not None:
            output.claim()
        try:
            self._process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE if output is not None else subprocess.DEVNULL,
                                             bufsize=0)
        except Exception:
            if output is not None:
                output.close()
            raise
        try:
            assert self._process.stdin is not None and self._process.stdout is not None
            # Transfer a duplicate to the writer, closing the Popen stream immediately.
            fd = os.dup(self._process.stdin.fileno())
            self._process.stdin.close()
            self._input = NonblockingInput(fd, clock=clock)
            os.set_blocking(self._process.stdout.fileno(), False)
            self._progress = self._process.stderr if output is not None else self._process.stdout
            assert self._progress is not None
            os.set_blocking(self._progress.fileno(), False)
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
        """One read per output pipe and one input write; bounded frame history."""
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
            assert self._progress is not None
            if self._output is not None:
                try:
                    media = os.read(self._process.stdout.fileno(), 16384)
                except (BlockingIOError, InterruptedError):
                    media = b""
                self._output.feed(media)
            try:
                chunk = os.read(self._progress.fileno(), 4096)
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
        if self._output is not None:
            self._output.close()
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
                for stream in (self._process.stdin, self._process.stdout,
                               getattr(self._process, "stderr", None)):
                    if stream is not None:
                        stream.close()
