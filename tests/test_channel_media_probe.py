import json
import struct
import threading

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from scripts import channel_media_probe as probe


def box(kind, payload=b""):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


@pytest.mark.parametrize("frame", [b"", "mdat", b"mdat", box(b"ftyp", b"isom"),
                                  box(b"mdat"), struct.pack(">I4s", 100, b"mdat")])
def test_init_or_malformed_frame_is_not_media(frame):
    assert not probe.contains_media(frame)


def test_negotiates_only_video_and_waits_for_media_not_init():
    class Socket:
        frames = iter([json.dumps({"type": "mse", "value": "video/mp4"}),
                       box(b"ftyp", b"isom"), box(b"moof") + box(b"mdat", b"data")])
        def __init__(self): self.sent = []
        def send(self, value): self.sent.append(json.loads(value))
        def recv(self, timeout): return next(self.frames)
    socket = Socket()
    assert probe.receive_media(socket, start=True) > 0
    assert socket.sent == [{"type": "mse", "value": "avc1.640029"}]


def test_revocation_allows_bounded_inflight_frames_then_requires_policy_close():
    class Socket:
        frames = 0
        def recv(self, timeout):
            if self.frames == 0:
                self.frames += 1
                return b"inflight"
            raise ConnectionClosedError(Close(1008, ""), Close(1008, ""), True)
    result = probe.drain_until_invalidated(Socket())
    assert result["inflight_bytes"] == 8 and result["closed_ms"] >= 0


def test_missing_close_is_not_reported_as_revocation():
    class Socket:
        def recv(self, timeout): raise ConnectionClosedError(None, None, None)
    with pytest.raises(RuntimeError): probe.drain_until_invalidated(Socket())


@pytest.mark.parametrize("operation", [probe.receive_media, probe.drain_until_invalidated])
def test_input_flood_is_bounded(monkeypatch, operation):
    monkeypatch.setattr(probe, "MAX_BYTES", 8)
    class Socket:
        def recv(self, timeout): return b"x" * 9
    with pytest.raises(RuntimeError): operation(Socket())


def test_independent_probe_drains_video_to_allow_pong_processing():
    class Socket:
        pong = threading.Event()
        def ping(self): return self.pong
        def recv(self, timeout):
            # Simulate a reader whose control frames cannot progress until drain.
            self.pong.set()
            return box(b"mdat", b"video")
    probe.verify_independent_flow(Socket())
