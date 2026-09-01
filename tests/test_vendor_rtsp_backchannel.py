from __future__ import annotations

import struct
from typing import ClassVar

import pytest

from backend.app.db.registry import Camera
from backend.app.drivers.yoosee import rtsp_backchannel as talk

CAMERA_ID = "cam_0123456789abcdef01234567"


def _camera() -> Camera:
    return Camera(
        camera_id=CAMERA_ID,
        last_ip="192.0.2.10",
        rtsp_port=554,
        stream_path="/onvif1",
        username="admin",
        password="secret",
    )


class FakeRtspSession:
    instances: ClassVar[list[FakeRtspSession]] = []

    def __init__(self, ip: str, port: int, timeout: float) -> None:
        self.init = (ip, port, timeout)
        self.requests: list[tuple[str, str, dict[str, object]]] = []
        self.frames: list[tuple[int, bytes]] = []
        self.closed = False
        self.instances.append(self)

    def request(self, method: str, uri: str, **kwargs: object) -> bytes:
        self.requests.append((method, uri, kwargs))
        if method == "OPTIONS":
            return b"RTSP/1.0 200 OK\r\nPublic: OPTIONS,USER_CMD_SET\r\n\r\n"
        return b"RTSP/1.0 200 OK\r\n\r\n"

    def send_interleaved(self, channel: int, payload: bytes) -> None:
        self.frames.append((channel, payload))

    def close(self) -> None:
        self.closed = True


def test_alaw_encoder_matches_ffmpeg_reference_values() -> None:
    samples = (
        -32768,
        -20000,
        -10000,
        -2051,
        -1000,
        -1,
        0,
        1,
        1000,
        2051,
        10000,
        20000,
        32767,
    )
    pcm = struct.pack("<" + "h" * len(samples), *samples)
    assert talk.pcm16le_to_alaw(pcm) == bytes.fromhex("2a26366a7a55d5d5faeab6a6aa")


def test_rtsp_talkback_uses_native_control_and_fixed_interleaved_rtp() -> None:
    FakeRtspSession.instances.clear()
    result = talk._send_chunks(
        _camera(),
        (bytes(320), bytes(320), bytes(320)),
        session_factory=FakeRtspSession,
        clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    )
    probe, session = FakeRtspSession.instances

    assert [request[0] for request in probe.requests] == ["DESCRIBE", "OPTIONS"]
    assert [request[0] for request in session.requests] == ["USER_CMD_SET", "USER_CMD_SET"]
    controls = [
        request[2]["extra_headers"]["AudioCtlCmd"]
        for request in session.requests
        if request[0] == "USER_CMD_SET"
    ]
    assert controls == ["OPEN", "CLOSE"]
    assert len(session.frames) == 2
    assert all(channel == 2 and len(packet) == 332 for channel, packet in session.frames)
    first, second = (packet for _channel, packet in session.frames)
    assert first[:2] == b"\x80\x88"
    assert second[:2] == b"\x80\x08"
    assert (
        struct.unpack("!I", second[4:8])[0]
        == (struct.unpack("!I", first[4:8])[0] + 320) & 0xFFFFFFFF
    )
    assert second[-160:] == bytes((talk.PCMA_SILENCE,)) * 160
    assert session.closed is True
    assert probe.closed is True
    assert result.duration_ms == 60
    assert result.completed is True


def test_rtsp_talkback_rejects_firmware_without_custom_method() -> None:
    class StandardRtsp(FakeRtspSession):
        def request(self, method: str, uri: str, **kwargs: object) -> bytes:
            self.requests.append((method, uri, kwargs))
            return b"RTSP/1.0 200 OK\r\nPublic: OPTIONS,DESCRIBE,PLAY\r\n\r\n"

    with pytest.raises(talk.RtspBackchannelError, match="does not advertise"):
        talk._send_chunks(
            _camera(),
            (bytes(320),),
            session_factory=StandardRtsp,
            clock=lambda: 0.0,
            sleep=lambda _seconds: None,
        )
