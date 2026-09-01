from __future__ import annotations

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.db.registry import Camera
from backend.app.drivers.contracts import AudioMessageResult, ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import audio
from backend.app.drivers.yoosee.p2p.audio_sender import LegacyAudioSendResult
from backend.app.drivers.yoosee.p2p.intercom import IntercomProbeResult
from backend.app.drivers.yoosee.p2p.intercom_session import IntercomControlResult

CAMERA_ID = "cam_0123456789abcdef01234567"


def _camera() -> Camera:
    return Camera(mac="aa:bb:cc:dd:ee:01", camera_id=CAMERA_ID)


def _enrollment() -> P2PEnrollment:
    return P2PEnrollment("7000000001", 123, bytes(64), None, "now", "now", CAMERA_ID)


def test_audio_adapter_maps_only_transport_neutral_delivery_state(monkeypatch) -> None:
    enrollment = _enrollment()
    transport = LegacyAudioSendResult(2, 2, 2, 5, False)
    probe = IntercomProbeResult(
        enrollment.device_id,
        True,
        True,
        True,
        1,
        IntercomControlResult(True, True, True, True, True, transport),
        True,
    )
    observed = []
    monkeypatch.setattr(
        audio.p2p,
        "get_enrollment_for_camera",
        lambda camera_id: enrollment if camera_id == CAMERA_ID else None,
    )
    monkeypatch.setattr(
        audio,
        "send_pcm_intercom",
        lambda selected, pcm: observed.append((selected.device_id, pcm)) or probe,
    )

    result = audio.send(_camera(), bytes(640))

    assert observed == [(enrollment.device_id, bytes(640))]
    assert result.duration_ms == 40
    assert result.public()["completed"] is True
    assert "device_id" not in result.public()


def test_streaming_audio_adapter_consumes_chunks_once_and_maps_result(monkeypatch) -> None:
    enrollment = _enrollment()
    transport = LegacyAudioSendResult(2, 2, 2, 5, False)
    probe = IntercomProbeResult(
        enrollment.device_id,
        True,
        True,
        True,
        1,
        IntercomControlResult(True, True, True, True, True, transport),
        True,
    )
    observed = []
    monkeypatch.setattr(
        audio.p2p,
        "get_enrollment_for_camera",
        lambda camera_id: enrollment if camera_id == CAMERA_ID else None,
    )

    def stream(selected, chunks):
        observed.append((selected.device_id, tuple(chunks)))
        return probe

    monkeypatch.setattr(audio, "send_pcm_intercom_chunks", stream)

    result = audio.send_stream(_camera(), iter((bytes(320), bytes(320))))

    assert observed == [(enrollment.device_id, (bytes(320), bytes(320)))]
    assert result.duration_ms == 40
    assert result.completed is True


def test_audio_adapter_requires_exact_camera_enrollment(monkeypatch) -> None:
    monkeypatch.setattr(audio.p2p, "get_enrollment_for_camera", lambda _camera_id: None)
    with pytest.raises(ControlNotReady):
        audio.send(_camera(), bytes(320))


def test_audio_adapter_normalizes_protocol_failures(monkeypatch) -> None:
    monkeypatch.setattr(audio.p2p, "get_enrollment_for_camera", lambda _camera_id: _enrollment())
    monkeypatch.setattr(
        audio,
        "send_pcm_intercom",
        lambda *_args: (_ for _ in ()).throw(ValueError("native detail")),
    )
    with pytest.raises(ControlOperationError, match="native detail"):
        audio.send(_camera(), bytes(320))


def test_audio_adapter_prefers_direct_rtsp_when_local_material_exists(monkeypatch) -> None:
    camera = _camera()
    camera.last_ip = "192.0.2.10"
    camera.stream_path = "/onvif1"
    expected = AudioMessageResult(20, 1, 1, 1, True, True, True)
    observed = []
    monkeypatch.setattr(
        audio.rtsp_backchannel,
        "send",
        lambda selected, pcm: observed.append((selected.camera_id, pcm)) or expected,
    )
    monkeypatch.setattr(
        audio,
        "send_pcm_intercom",
        lambda *_args: (_ for _ in ()).throw(AssertionError("P2P fallback must not run")),
    )

    assert audio.send(camera, bytes(320)) is expected
    assert observed == [(CAMERA_ID, bytes(320))]


@pytest.mark.parametrize(
    "result",
    [
        (21, 1, 1, 1),
        (20, 1, 2, 1),
        (20, 1, 1, 2),
        (20, 501, 501, 501),
    ],
)
def test_generic_audio_result_rejects_inconsistent_bounds(result) -> None:
    with pytest.raises(ValueError):
        AudioMessageResult(*result, True, True, True)
