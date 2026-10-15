"""Bounded MSE delivery checks. No decoding, capture files or audio output."""
import json
import struct
import time

from websockets.exceptions import ConnectionClosed

MAX_BYTES = 16 * 1024 * 1024


def contains_media(frame):
    """Require complete top-level fMP4 media data, not just an init segment."""
    if not isinstance(frame, bytes):
        return False
    offset = 0
    while offset + 8 <= len(frame):
        size, kind = struct.unpack_from(">I4s", frame, offset)
        if size < 8 or size > len(frame) - offset:
            return False
        if kind == b"mdat" and size > 8:
            return True
        offset += size
    return False


def receive_media(socket, *, start=False):
    if start:
        # One video-only request; never microphone PCM or arbitrary upstream text.
        socket.send(json.dumps({"type": "mse", "value": "avc1.640029"}))
    # A cold on-demand encoder may need more than one GOP to produce its init
    # segment. This is startup allowance, not a longer revocation deadline.
    deadline = time.monotonic() + 15
    received = 0
    for _ in range(256):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        frame = socket.recv(timeout=remaining)
        if isinstance(frame, str) and json.loads(frame).get("type") == "error":
            raise RuntimeError("Media source rejected probe")
        received += len(frame)
        if received > MAX_BYTES:
            raise RuntimeError("Media probe input limit")
        if contains_media(frame):
            return received
    raise RuntimeError("No media fragment within probe budget")


def drain_until_invalidated(socket):
    """Allow bounded buffered/in-flight bytes; never claim instantaneous recall."""
    started = time.monotonic()
    deadline = started + 7
    received = 0
    for _ in range(1024):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            frame = socket.recv(timeout=remaining)
        except ConnectionClosed as exc:
            if exc.rcvd is None or exc.rcvd.code != 1008:
                raise RuntimeError("Missing authorization close frame") from None
            return {"closed_ms": round((time.monotonic() - started) * 1000),
                    "inflight_bytes": received}
        received += len(frame)
        if received > MAX_BYTES:
            break
    raise RuntimeError("Active channel did not close within probe budget")


def verify_independent_flow(socket):
    """Drain media while awaiting pong; a full bounded queue blocks control reads."""
    pong = socket.ping()
    deadline = time.monotonic() + 3
    received = 0
    media = False
    for _ in range(512):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or received > MAX_BYTES:
            break
        try:
            frame = socket.recv(timeout=min(0.2, remaining))
        except TimeoutError:
            if media and pong.is_set():
                return
            continue
        received += len(frame)
        media |= contains_media(frame)
        if media and pong.is_set():
            return
    raise RuntimeError("Independent channel lost")
