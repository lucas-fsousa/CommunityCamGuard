"""Bounded incremental PCM-to-AAC encoder for IoTVideo intercom."""

from __future__ import annotations

import math
import os
import select
import subprocess
import time

INPUT_RATE = 8_000
OUTPUT_RATE = 16_000
INPUT_FRAME_BYTES = 320
AAC_FRAME_INTERVAL_SECONDS = 0.064
MAX_SECONDS = 10.0


def _adts_frame_length(header: bytes | bytearray) -> int:
    return ((header[3] & 0x03) << 11) | (header[4] << 3) | (header[5] >> 5)


def validate_aac_lc_adts_frame(frame: bytes) -> None:
    """Accept only the MPEG-4 AAC-LC/16 kHz/mono wire format used by the SDK."""

    if len(frame) < 7 or frame[:2] != b"\xff\xf1":
        raise ValueError("IoTVideo talk requires MPEG-4 ADTS frames")
    profile = frame[2] >> 6
    sample_rate_index = (frame[2] >> 2) & 0x0F
    channels = ((frame[2] & 1) << 2) | (frame[3] >> 6)
    if profile != 1 or sample_rate_index != 8 or channels != 1:
        raise ValueError("IoTVideo talk requires AAC-LC, 16 kHz, mono")
    if frame[6] & 0x03 or _adts_frame_length(frame) != len(frame):
        raise ValueError("invalid or multi-block ADTS frame")


def extract_adts_frames(buffer: bytearray, *, final: bool = False) -> tuple[bytes, ...]:
    """Remove every complete ADTS frame from a mutable FFmpeg output buffer."""

    frames: list[bytes] = []
    cursor = 0
    while len(buffer) - cursor >= 7:
        if buffer[cursor : cursor + 2] != b"\xff\xf1":
            raise ValueError("AAC encoder returned an unexpected ADTS stream")
        length = _adts_frame_length(buffer[cursor : cursor + 7])
        if length < 7:
            raise ValueError("AAC encoder returned an invalid ADTS length")
        if cursor + length > len(buffer):
            break
        frame = bytes(buffer[cursor : cursor + length])
        validate_aac_lc_adts_frame(frame)
        frames.append(frame)
        cursor += length
    if cursor:
        del buffer[:cursor]
    if final and buffer:
        raise ValueError("AAC encoder returned a truncated ADTS frame")
    return tuple(frames)


class AacLcAdtsEncoder:
    """Keep one low-latency FFmpeg encoder alive for a push-to-talk utterance."""

    def __init__(self, *, max_seconds: float = MAX_SECONDS) -> None:
        if not 0.1 <= max_seconds <= MAX_SECONDS:
            raise ValueError("AAC intercom limit must be between 0.1 and 10 seconds")
        self._max_input_bytes = int(max_seconds * INPUT_RATE * 2)
        self._max_frames = math.ceil(max_seconds / AAC_FRAME_INTERVAL_SECONDS) + 3
        self._input_bytes = 0
        self._frame_count = 0
        self._buffer = bytearray()
        self._finalized = False
        self._closed = False
        command = (
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(INPUT_RATE),
            "-ac",
            "1",
            "-probesize",
            "32",
            "-analyzeduration",
            "0",
            "-blocksize",
            str(INPUT_FRAME_BYTES),
            "-i",
            "pipe:0",
            "-ar",
            str(OUTPUT_RATE),
            "-ac",
            "1",
            "-c:a",
            "aac",
            "-profile:a",
            "aac_low",
            "-b:a",
            "32k",
            "-flush_packets",
            "1",
            "-f",
            "adts",
            "-write_mpeg2",
            "0",
            "pipe:1",
        )
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as exc:
            raise RuntimeError("FFmpeg AAC encoder is unavailable") from exc
        assert self._process.stdin is not None and self._process.stdout is not None
        os.set_blocking(self._process.stdout.fileno(), False)

    def _collect(self, wait_seconds: float) -> tuple[bytes, ...]:
        stdout = self._process.stdout
        assert stdout is not None
        ready, _, _ = select.select((stdout,), (), (), max(0.0, wait_seconds))
        while ready:
            chunk = os.read(stdout.fileno(), 65_536)
            if not chunk:
                break
            self._buffer.extend(chunk)
            ready, _, _ = select.select((stdout,), (), (), 0)
        frames = extract_adts_frames(self._buffer)
        self._frame_count += len(frames)
        if self._frame_count > self._max_frames:
            raise ValueError("AAC encoder exceeded the intercom frame bound")
        return frames

    def _error(self) -> RuntimeError:
        stderr = self._process.stderr
        detail = stderr.read(512).decode("utf-8", "replace").strip() if stderr else ""
        return RuntimeError(f"FFmpeg AAC encoding failed{': ' + detail if detail else ''}")

    def feed(self, pcm16le: bytes, *, final: bool = False) -> tuple[bytes, ...]:
        if self._closed:
            raise RuntimeError("AAC encoder is closed")
        if self._finalized:
            raise RuntimeError("AAC encoder is already finalized")
        if len(pcm16le) % 2:
            raise ValueError("PCM input must contain complete signed 16-bit samples")
        if self._input_bytes + len(pcm16le) > self._max_input_bytes:
            raise ValueError("AAC intercom input exceeds the configured safety bound")
        stdin = self._process.stdin
        assert stdin is not None
        if pcm16le:
            try:
                stdin.write(pcm16le)
                stdin.flush()
            except BrokenPipeError as exc:
                raise self._error() from exc
            self._input_bytes += len(pcm16le)
        emitted = list(self._collect(0.01))
        if not final:
            return tuple(emitted)

        self._finalized = True
        stdin.close()
        deadline = time.monotonic() + 5.0
        while self._process.poll() is None and time.monotonic() < deadline:
            emitted.extend(self._collect(0.1))
        if self._process.poll() is None:
            self._process.kill()
            self._process.wait(timeout=1)
            raise RuntimeError("FFmpeg AAC encoder did not stop")
        emitted.extend(self._collect(0))
        if self._process.returncode:
            raise self._error()
        trailing = extract_adts_frames(self._buffer, final=True)
        self._frame_count += len(trailing)
        emitted.extend(trailing)
        if self._frame_count > self._max_frames:
            raise ValueError("AAC encoder exceeded the intercom frame bound")
        return tuple(emitted)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1)


def encode_pcm16le(pcm16le: bytes, *, max_seconds: float = MAX_SECONDS) -> tuple[bytes, ...]:
    """Encode one complete bounded 8 kHz PCM message for IoTVideo talk."""

    if not pcm16le:
        raise ValueError("AAC intercom PCM input is empty")
    encoder = AacLcAdtsEncoder(max_seconds=max_seconds)
    try:
        return encoder.feed(pcm16le, final=True)
    finally:
        encoder.close()
