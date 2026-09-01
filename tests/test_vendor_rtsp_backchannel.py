from __future__ import annotations

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


def test_rtsp_talkback_uses_native_control_and_fixed_interleaved_rtp() -> None:
    FakeRtspSession.instances.clear()
    chunks = (
        bytes(640),
        (bytes(range(256)) + bytes(range(64))) * 2,
        bytes([0xA5]) * 640,
    )
    result = talk._send_chunks(
        _camera(),
        chunks,
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
    assert len(session.frames) == 6
    assert all(channel == 2 and len(packet) == 332 for channel, packet in session.frames)
    first, second, third, fourth, fifth, sixth = (
        packet for _channel, packet in session.frames
    )
    assert first[:2] == b"\x80\x88"
    assert second[:2] == b"\x80\x08"
    assert all(packet[:2] == b"\x80\x08" for packet in (third, fourth, fifth, sixth))
    assert (
        int.from_bytes(second[4:8], "big")
        == (int.from_bytes(first[4:8], "big") + 160) & 0xFFFFFFFF
    )
    assert first[12:] == chunks[0][:320]
    assert second[12:] == chunks[0][320:]
    assert third[12:] == chunks[1][:320]
    assert fourth[12:] == chunks[1][320:]
    assert fifth[12:] == chunks[2][:320]
    assert sixth[12:] == chunks[2][320:]
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
            (bytes(640),),
            session_factory=StandardRtsp,
            clock=lambda: 0.0,
            sleep=lambda _seconds: None,
        )


def test_rtsp_talkback_preloads_and_recovers_scheduler_delay_from_jitter_buffer() -> None:
    class LateClock:
        def __init__(self) -> None:
            self.now = 0.0
            self.sleeps: list[float] = []
            self.late_once = True

        def __call__(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds
            if self.late_once:
                self.now += 0.030  # Simulate one scheduler wake-up that is 30 ms late.
                self.late_once = False

    clock = LateClock()
    FakeRtspSession.instances.clear()
    talk._send_chunks(
        _camera(),
        (bytes(640) for _index in range(7)),
        session_factory=FakeRtspSession,
        clock=clock,
        sleep=clock.sleep,
    )

    # Ten 10 ms packets are sent immediately. The delayed wake-up is absorbed by that 100 ms
    # lead, so packets whose deadlines elapsed are grouped and only the final drain waits again.
    assert clock.sleeps == pytest.approx([0.010, 0.100])
