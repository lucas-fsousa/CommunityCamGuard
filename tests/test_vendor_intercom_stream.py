from __future__ import annotations

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import intercom_stream
from backend.app.drivers.yoosee.p2p.audio_sender import LegacyAudioSendResult
from backend.app.drivers.yoosee.p2p.av_session import AvSessionResult
from backend.app.drivers.yoosee.p2p.contracts import (
    CallingAttempt,
    CallingResult,
    CertifiedNode,
    OnlineDevice,
)
from backend.app.drivers.yoosee.p2p.intercom_session import IntercomControlResult
from backend.app.drivers.yoosee.p2p.media_session import MediaChannelResult

AMR = bytes.fromhex("3c") + bytes(31)


def _fixture():
    enrollment = P2PEnrollment("7443576841", 123, bytes(range(64)), None, "now", "now", "cam_test")
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7_443_576_841, 1, False, 1, bytes(16))
    attempt = CallingAttempt(0x123456, 0x89ABCDEF, b"12345678")
    calling = CallingResult(
        True, True, 3, True, None, ("198.51.100.9", 32100), 18, attempt.link_id, attempt
    )
    av = AvSessionResult(3, (2, 6), 4, 4, (), 1, None)
    return enrollment, node, target, calling, av


class FakeSocket:
    def __init__(self) -> None:
        self.closed = False

    def bind(self, _address):
        pass

    def close(self):
        self.closed = True


class FakeEncoder:
    def __init__(self, *, max_seconds):
        self.max_seconds = max_seconds
        self.closed = False

    def feed(self, pcm, *, final=False):
        if final:
            return ()
        return (AMR,) if pcm else ()

    def close(self):
        self.closed = True


class FakeControl:
    def __init__(self, *_args, **_kwargs):
        self.frames = []
        self.closed = False

    def start(self):
        return True

    def send_audio_frame(self, frame):
        self.frames.append(frame)
        return True

    def result(self):
        audio = (
            LegacyAudioSendResult(
                len(self.frames), len(self.frames), len(self.frames), 3 + len(self.frames), False
            )
            if self.frames
            else None
        )
        return IntercomControlResult(True, True, True, self.closed, self.closed, audio)

    def close(self):
        self.closed = True
        return self.result()


def test_stream_owns_route_encoder_and_control_until_close(monkeypatch) -> None:
    enrollment, node, target, calling, av = _fixture()
    sock = FakeSocket()
    controls = []
    released = []
    monkeypatch.setattr(intercom_stream.socket, "socket", lambda *_args: sock)
    monkeypatch.setattr(intercom_stream, "open_camera_session", lambda *_args: (node, target, 17))
    monkeypatch.setattr(intercom_stream, "call_device", lambda *_args, **_kwargs: calling)
    monkeypatch.setattr(
        intercom_stream, "open_media_channel", lambda *_args: MediaChannelResult(False, True, 4)
    )
    monkeypatch.setattr(intercom_stream, "initialize_av_session", lambda *_args: av)
    monkeypatch.setattr(intercom_stream, "AmrNbEncoder", FakeEncoder)

    def control(*args, **kwargs):
        instance = FakeControl(*args, **kwargs)
        controls.append(instance)
        return instance

    monkeypatch.setattr(intercom_stream, "ModernIntercomSession", control)
    monkeypatch.setattr(
        intercom_stream,
        "close_device_route",
        lambda *_args: released.append(calling.route_link_id) or True,
    )

    stream = intercom_stream.PcmIntercomStream(enrollment, max_seconds=1.0)
    assert stream.start() is True
    assert stream.feed_pcm16le(bytes(320)) == 1
    assert stream.feed_pcm16le(bytes(320)) == 1
    result = stream.close(flush=True)

    assert controls[0].frames == [AMR, AMR]
    assert controls[0].closed is True
    assert released == [calling.route_link_id]
    assert sock.closed is True
    assert result.completed is True
    assert stream.close() == result


def test_chunk_orchestrator_serializes_and_releases_after_invalid_input(monkeypatch) -> None:
    enrollment, _node, _target, _calling, _av = _fixture()
    events = []

    class FakeStream:
        def __init__(self, selected, **_kwargs):
            events.append(("create", selected.device_id))

        def start(self):
            events.append("start")
            return True

        def feed_pcm16le(self, chunk):
            events.append(("feed", chunk))

        def close(self, *, flush=False):
            events.append(("close", flush))
            return intercom_stream.empty_intercom_result(enrollment.device_id)

    monkeypatch.setattr(intercom_stream, "PcmIntercomStream", FakeStream)
    monkeypatch.setattr(
        intercom_stream,
        "run_with_fresh_access",
        lambda selected, operation: events.append("lock") or operation(selected),
    )

    with pytest.raises(intercom_stream.P2PProbeError):
        intercom_stream.send_pcm_intercom_chunks(enrollment, (bytes(320), b""))
    assert events == [
        "lock",
        ("create", enrollment.device_id),
        "start",
        ("feed", bytes(320)),
        ("close", False),
    ]
