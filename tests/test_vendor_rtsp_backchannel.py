from __future__ import annotations

import logging
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


@pytest.mark.parametrize("public", [
    "OPTIONS,USER_CMD_SET_V2", "OPTIONS,X_USER_CMD_SET", "USER_CMD_SET PLAY", "",
])
def test_rtsp_talkback_rejects_similar_method_without_opening(public: str) -> None:
    class SimilarMethod(FakeRtspSession):
        def request(self, method: str, uri: str, **kwargs: object) -> bytes:
            self.requests.append((method, uri, kwargs))
            return f"RTSP/1.0 200 OK\r\nPublic: {public}\r\n\r\n".encode()

    FakeRtspSession.instances.clear()
    with pytest.raises(talk.RtspBackchannelError, match="does not advertise"):
        talk._send_chunks(_camera(), (bytes(640),), session_factory=SimilarMethod)
    assert len(FakeRtspSession.instances) == 1
    probe = FakeRtspSession.instances[0]
    assert [request[0] for request in probe.requests] == ["DESCRIBE", "OPTIONS"]
    assert probe.closed and not probe.frames


@pytest.mark.parametrize("public", ["USER_CMD_SET", " OPTIONS, user_cmd_set , PLAY "])
def test_rtsp_talkback_accepts_exact_method_with_whitespace(public: str) -> None:
    class ExactMethod(FakeRtspSession):
        def request(self, method: str, uri: str, **kwargs: object) -> bytes:
            if method == "OPTIONS":
                self.requests.append((method, uri, kwargs))
                return f"RTSP/1.0 200 OK\r\nPublic: {public}\r\n\r\n".encode()
            return super().request(method, uri, **kwargs)

    FakeRtspSession.instances.clear()
    result = talk._send_chunks(
        _camera(), (bytes(640),), session_factory=ExactMethod,
        clock=lambda: 0.0, sleep=lambda _seconds: None,
    )
    assert result.completed
    assert len(FakeRtspSession.instances) == 2
    assert all(session.closed for session in FakeRtspSession.instances)


@pytest.mark.parametrize("fail_refresh", [False, True])
def test_rtsp_talkback_watchdog_and_cleanup_with_virtual_time(fail_refresh: bool, caplog) -> None:
    caplog.set_level(logging.INFO, logger="backend.app.drivers.yoosee.audio_timing")
    now = [0.0]
    opens: list[float] = []

    class WatchdogSession(FakeRtspSession):
        def request(self, method: str, uri: str, **kwargs: object) -> bytes:
            headers = kwargs.get("extra_headers")
            if isinstance(headers, dict) and headers.get("AudioCtlCmd") == "OPEN":
                opens.append(now[0])
                if fail_refresh and len(opens) == 2:
                    self.requests.append((method, uri, kwargs))
                    return b"RTSP/1.0 500 Error\r\n\r\n"
            return super().request(method, uri, **kwargs)

    def advance(seconds: float) -> None:
        now[0] += seconds

    def send() -> talk.AudioMessageResult:
        return talk._send_chunks(
            _camera(), (bytes(640) for _ in range(500)),
            session_factory=WatchdogSession, clock=lambda: now[0], sleep=advance,
        )

    FakeRtspSession.instances.clear()
    if fail_refresh:
        with pytest.raises(talk.RtspBackchannelError, match="rejected RTSP audio open"):
            send()
        assert len(opens) == 2
    else:
        result = send()
        assert result.completed and result.duration_ms == 10_000
        assert opens == pytest.approx([0.0, 4.0, 8.0], abs=0.011)
        assert now[0] == pytest.approx(10.0)
    probe, media = FakeRtspSession.instances
    assert probe.closed and media.closed
    assert media.requests[-1][2]["extra_headers"]["AudioCtlCmd"] == "CLOSE"
    if fail_refresh:
        assert len(media.frames) < 1000
    else:
        assert len(media.frames) == 1000
    summaries = [record.getMessage() for record in caplog.records
                 if record.name == "backend.app.drivers.yoosee.audio_timing"]
    assert len(summaries) == 1
    assert f"packets={len(media.frames)} " in summaries[0]
    assert "opened=True released=True" in summaries[0]
    assert "secret" not in summaries[0]


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


@pytest.mark.parametrize("fault", [None, "refresh", "write"])
def test_timing_separates_refresh_from_media_write_and_preserves_close(fault):
    now = [0.0]
    class DelayedSession(FakeRtspSession):
        def request(self, method, uri, **kwargs):
            headers = kwargs.get("extra_headers") or {}
            if headers.get("AudioCtlCmd") == "OPEN" and now[0] >= 4:
                now[0] += 0.240
                if fault == "refresh":
                    raise OSError("refresh failure")
            return super().request(method, uri, **kwargs)

        def send_interleaved(self, channel, payload):
            now[0] += 0.007
            if fault == "write":
                raise OSError("write failure")
            super().send_interleaved(channel, payload)

    FakeRtspSession.instances.clear()
    stats = talk.DeliveryTiming()
    session = talk.RtspTalkSession(_camera(), session_factory=DelayedSession,
                                   clock=lambda: now[0], timing=stats)
    session.open()
    assert stats.refresh_calls == 0  # Initial OPEN is not a mid-audio refresh.
    now[0] = 4.1
    try:
        if fault:
            with pytest.raises(OSError):
                session.send_pcm(bytes(320))
        else:
            session.send_pcm(bytes(320))
    finally:
        assert session.close()
    assert stats.refresh_calls == 1
    assert stats.refresh_failures == int(fault == "refresh")
    assert stats.max_refresh_ms == pytest.approx(240)
    assert stats.write_calls == int(fault != "refresh")
    assert stats.write_failures == int(fault == "write")
    assert stats.max_write_ms == pytest.approx(0 if fault == "refresh" else 7)
    assert all(item.closed for item in FakeRtspSession.instances)
