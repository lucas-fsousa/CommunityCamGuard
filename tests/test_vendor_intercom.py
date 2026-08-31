from __future__ import annotations

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import intercom
from backend.app.drivers.yoosee.p2p.audio_sender import LegacyAudioSendResult
from backend.app.drivers.yoosee.p2p.av_session import AvSessionResult
from backend.app.drivers.yoosee.p2p.contracts import (
    CallingAttempt,
    CallingResult,
    CertifiedNode,
    OnlineDevice,
    P2PProbeError,
)
from backend.app.drivers.yoosee.p2p.intercom_session import IntercomControlResult
from backend.app.drivers.yoosee.p2p.media_session import MediaChannelResult


def _fixture():
    enrollment = P2PEnrollment(
        "7443576841", 123, bytes(range(64)), None, "now", "now", "cam_0acab4738d6f7e2729ec6467"
    )
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    target = OnlineDevice(7_443_576_841, 1, False, 1, bytes(16))
    attempt = CallingAttempt(0x123456, 0x89ABCDEF, b"12345678")
    calling = CallingResult(
        True,
        True,
        3,
        True,
        None,
        ("198.51.100.9", 32100),
        18,
        attempt.link_id,
        attempt,
    )
    return enrollment, node, target, calling


class FakeSocket:
    def bind(self, _address):
        pass

    def close(self):
        pass


def test_complete_silent_probe_releases_exact_route(monkeypatch) -> None:
    enrollment, node, target, calling = _fixture()
    control = IntercomControlResult(True, True, True, True, True)
    closed = []
    monkeypatch.setattr(intercom.socket, "socket", lambda *_args: FakeSocket())
    monkeypatch.setattr(intercom, "open_camera_session", lambda *_args: (node, target, 17))
    monkeypatch.setattr(intercom, "call_device", lambda *_args, **_kwargs: calling)
    monkeypatch.setattr(
        intercom, "open_media_channel", lambda *_args: MediaChannelResult(False, True, 4)
    )
    monkeypatch.setattr(
        intercom,
        "initialize_av_session",
        lambda *_args: AvSessionResult(3, (2, 6), 4, 4, (), 1, None),
    )
    monkeypatch.setattr(
        intercom,
        "run_modern_intercom_control",
        lambda *_args, **_kwargs: control,
    )
    monkeypatch.setattr(
        intercom,
        "close_device_route",
        lambda _sock, _node, _access, selected, link, sequence, _timeout: (
            closed.append((selected.device_id, link, sequence)) or True
        ),
    )

    result = intercom._probe_silent_intercom(enrollment, timeout=0.5, total_timeout=8)

    assert result.completed is True
    assert result.device_id == enrollment.device_id
    assert closed == [(target.device_id, calling.route_link_id, calling.next_sequence + 1)]


def test_probe_releases_route_when_media_stage_raises(monkeypatch) -> None:
    enrollment, node, target, calling = _fixture()
    closed = []
    monkeypatch.setattr(intercom.socket, "socket", lambda *_args: FakeSocket())
    monkeypatch.setattr(intercom, "open_camera_session", lambda *_args: (node, target, 17))
    monkeypatch.setattr(intercom, "call_device", lambda *_args, **_kwargs: calling)
    monkeypatch.setattr(
        intercom,
        "open_media_channel",
        lambda *_args: (_ for _ in ()).throw(ValueError("bad media")),
    )
    monkeypatch.setattr(
        intercom,
        "close_device_route",
        lambda *_args: closed.append(calling.route_link_id) or True,
    )

    with pytest.raises(P2PProbeError, match="silent P2P intercom probe failed"):
        intercom._probe_silent_intercom(enrollment, timeout=0.5, total_timeout=8)

    assert closed == [calling.route_link_id]


def test_public_probe_uses_serialized_renewal_boundary(monkeypatch) -> None:
    enrollment, _node, _target, _calling = _fixture()
    expected = intercom._empty_result(enrollment.device_id)
    seen = []
    monkeypatch.setattr(
        intercom,
        "run_with_fresh_access",
        lambda selected, operation: seen.append(selected.device_id) or operation(selected),
    )
    monkeypatch.setattr(intercom, "_probe_silent_intercom", lambda *_args, **_kwargs: expected)

    assert intercom.probe_silent_intercom(enrollment) == expected
    assert seen == [enrollment.device_id]


def test_audio_probe_uses_encoded_frames_and_releases_exact_route(monkeypatch) -> None:
    enrollment, node, target, calling = _fixture()
    audio = LegacyAudioSendResult(2, 2, 2, 5, False)
    control = IntercomControlResult(True, True, True, True, True, audio)
    frames = (bytes.fromhex("3c") + bytes(31),) * 2
    observed = []
    closed = []
    monkeypatch.setattr(intercom.socket, "socket", lambda *_args: FakeSocket())
    monkeypatch.setattr(intercom, "open_camera_session", lambda *_args: (node, target, 17))
    monkeypatch.setattr(intercom, "call_device", lambda *_args, **_kwargs: calling)
    monkeypatch.setattr(
        intercom, "open_media_channel", lambda *_args: MediaChannelResult(False, True, 4)
    )
    monkeypatch.setattr(
        intercom,
        "initialize_av_session",
        lambda *_args: AvSessionResult(3, (2, 6), 4, 4, (), 1, None),
    )

    def run_control(_sock, selected_calling, _av, _timeout, *, audio_frames):
        observed.append((selected_calling.attempt, audio_frames))
        return control

    monkeypatch.setattr(intercom, "run_modern_intercom_control", run_control)
    monkeypatch.setattr(
        intercom,
        "close_device_route",
        lambda _sock, _node, _access, selected, link, sequence, _timeout: (
            closed.append((selected.device_id, link, sequence)) or True
        ),
    )

    result = intercom._probe_intercom(
        enrollment,
        timeout=0.5,
        total_timeout=8,
        audio_frames=frames,
        failure_message="audio failed",
    )

    assert result.completed is True
    assert observed == [(calling.attempt, frames)]
    assert closed == [(target.device_id, calling.route_link_id, calling.next_sequence + 1)]


def test_pcm_entrypoint_encodes_before_serialized_operation(monkeypatch) -> None:
    enrollment, _node, _target, _calling = _fixture()
    frames = (bytes.fromhex("3c") + bytes(31),)
    expected = intercom._empty_result(enrollment.device_id)
    events = []

    def encode(pcm, *, max_seconds):
        events.append(("encode", pcm, max_seconds))
        return frames

    def serialize(selected, operation):
        events.append(("serialize", selected.device_id))
        return operation(selected)

    def probe(selected, **kwargs):
        events.append(("probe", selected.device_id, kwargs["audio_frames"]))
        return expected

    monkeypatch.setattr(intercom, "encode_pcm16le_aac", encode)
    monkeypatch.setattr(intercom, "run_with_fresh_access", serialize)
    monkeypatch.setattr(intercom, "_probe_intercom", probe)

    assert intercom.send_pcm_intercom(enrollment, bytes(320), max_seconds=1.0) == expected
    assert events == [
        ("encode", bytes(320), 1.0),
        ("serialize", enrollment.device_id),
        ("probe", enrollment.device_id, frames),
    ]
