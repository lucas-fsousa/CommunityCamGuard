from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from backend.app.api import onboarding
from backend.app.api.local_only import require_local_request
from backend.app.db import p2p, registry
from backend.app.discovery.active_scan import ScannedHost
from backend.app.provisioning import rtsp_completion
from backend.app.vendor_p2p.rtsp_setup import P2PRtspPreparation

MAC = "aa:bb:cc:dd:ee:03"
DEVICE = "7000000003"
TOKEN = "ab" * 64


@pytest.fixture(autouse=True)
def _init_registry():
    registry.init_db()


def _enrollment(camera_id=None):
    return p2p.upsert_enrollment(
        DEVICE,
        access_id=123,
        access_token=bytes(range(64)),
        dev_token=TOKEN,
        camera_id=camera_id,
    )


def _located():
    return rtsp_completion.LocatedCamera(
        "192.168.1.30", MAC, (5000,), "Yoosee", "IPC", "40.1.14"
    )


def _proof():
    return rtsp_completion.RtspMediaProof(
        "/onvif1", "udp", True, True, "h264", "pcm_alaw", 42
    )


def test_media_proof_requires_received_video_packets():
    valid = json.dumps(
        {"streams": [
            {"codec_type": "video", "codec_name": "h264", "nb_read_packets": "12"},
            {"codec_type": "audio", "codec_name": "pcm_alaw", "nb_read_packets": "4"},
        ]}
    ).encode()
    no_packets = json.dumps(
        {"streams": [{"codec_type": "video", "codec_name": "h264", "nb_read_packets": "0"}]}
    ).encode()

    proof = rtsp_completion._parse_media_proof(valid, "/onvif1", "udp")

    assert proof is not None and proof.packet_count == 16
    assert proof.has_video is True and proof.has_audio is True
    assert rtsp_completion._parse_media_proof(no_packets, "/onvif1", "udp") is None


def test_prove_rtsp_media_prefers_udp_and_never_returns_credential(monkeypatch):
    calls = []

    def fake_run(arguments, timeout):
        calls.append((arguments, timeout))
        payload = json.dumps(
            {"streams": [{
                "codec_type": "video", "codec_name": "h264", "nb_read_packets": "3"
            }]}
        ).encode()
        return subprocess.CompletedProcess(arguments, 0, payload, b"")

    monkeypatch.setattr(rtsp_completion, "_run_ffprobe", fake_run)

    proof = rtsp_completion.prove_rtsp_media(
        "192.168.1.30", "admin", "SafePass123", attempts=1
    )

    assert proof.transport == "udp" and proof.path == "/onvif1"
    assert calls[0][0][calls[0][0].index("-rtsp_transport") + 1] == "udp"
    assert "SafePass123" not in repr(proof)


def test_locate_camera_matches_only_exact_mac(monkeypatch):
    monkeypatch.setattr(
        rtsp_completion.active_scan,
        "scan",
        lambda **_kwargs: [
            ScannedHost("192.168.1.20", mac="aa:bb:cc:dd:ee:02", open_ports=[554]),
            ScannedHost(
                "192.168.1.30",
                mac=MAC,
                open_ports=[5000],
                vendor="Yoosee",
                model="IPC",
            ),
        ],
    )

    located = rtsp_completion.locate_camera_by_mac(MAC)

    assert located.ip == "192.168.1.30"
    assert located.mac == MAC


def test_completion_persists_only_after_media_proof(monkeypatch):
    enrollment = _enrollment()
    monkeypatch.setattr(rtsp_completion, "generate_rtsp_password", lambda: "SafePass123")
    monkeypatch.setattr(
        rtsp_completion,
        "prepare_camera_rtsp",
        lambda _enrollment, _password: P2PRtspPreparation(
            DEVICE, False, True, True, False
        ),
    )
    monkeypatch.setattr(rtsp_completion, "prove_rtsp_media", lambda *_args, **_kwargs: _proof())

    result = rtsp_completion.complete_camera_onboarding(
        enrollment, _located(), device_id=DEVICE, name="Garage test"
    )

    stored = registry.get_camera(MAC)
    assert stored is not None
    assert stored.camera_id == result.camera.camera_id
    assert stored.name == "Garage test"
    assert stored.password == "SafePass123"
    assert stored.stream_path == "/onvif1"
    assert p2p.get_enrollment(DEVICE).camera_id == stored.camera_id
    assert result.already_configured is False
    assert result.stages[-2:] == ("media_proof", "registry")


def test_failed_media_proof_rolls_back_enable_and_does_not_register(monkeypatch):
    enrollment = _enrollment()
    rollback = []
    monkeypatch.setattr(rtsp_completion, "generate_rtsp_password", lambda: "SafePass123")
    monkeypatch.setattr(
        rtsp_completion,
        "prepare_camera_rtsp",
        lambda _enrollment, _password: P2PRtspPreparation(
            DEVICE, False, True, True, False
        ),
    )
    monkeypatch.setattr(
        rtsp_completion,
        "prove_rtsp_media",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            rtsp_completion.OnboardingCompletionError("media_proof", "no packets")
        ),
    )
    monkeypatch.setattr(
        rtsp_completion,
        "set_camera_rtsp_enabled",
        lambda _enrollment, enabled: rollback.append(enabled),
    )

    with pytest.raises(rtsp_completion.OnboardingCompletionError, match="no packets"):
        rtsp_completion.complete_camera_onboarding(
            enrollment, _located(), device_id=DEVICE
        )

    assert rollback == [False]
    assert registry.get_camera(MAC) is None


def test_existing_verified_camera_is_idempotent_and_never_rotates_password(monkeypatch):
    camera = registry.upsert_camera(
        MAC,
        name="Existing",
        username="admin",
        password="Existing123",
        stream_path="/onvif1",
        last_ip="192.168.1.30",
    )
    enrollment = _enrollment(camera.camera_id)
    monkeypatch.setattr(rtsp_completion, "prove_rtsp_media", lambda *_args, **_kwargs: _proof())
    monkeypatch.setattr(
        rtsp_completion,
        "prepare_camera_rtsp",
        lambda *_args: (_ for _ in ()).throw(AssertionError("credential rotated")),
    )

    result = rtsp_completion.complete_camera_onboarding(
        enrollment, _located(), device_id=DEVICE
    )

    assert result.already_configured is True
    assert registry.get_camera(MAC).password == "Existing123"


def test_completion_api_returns_public_result_without_credentials_or_native_id(monkeypatch):
    camera = registry.upsert_camera(
        MAC,
        name="Test camera",
        username="admin",
        password="SafePass123",
        stream_path="/onvif1",
        last_ip="192.168.1.30",
    )
    enrollment = _enrollment(camera.camera_id)
    completed = rtsp_completion.CompletedCamera(
        camera,
        _proof(),
        ("identity", "media_proof", "registry"),
        False,
    )
    monkeypatch.setattr(
        onboarding,
        "inspect_label",
        lambda **_kwargs: {
            "device_id": DEVICE,
            "mac": MAC,
            "firmware_version": "40.1.14",
        },
    )
    monkeypatch.setattr(onboarding, "bound_privileged_enrollment", lambda _device: enrollment)
    monkeypatch.setattr(onboarding, "locate_camera_by_mac", lambda _mac: _located())
    monkeypatch.setattr(
        onboarding, "complete_camera_onboarding", lambda *_args, **_kwargs: completed
    )
    media = SimpleNamespace(restart=lambda: None, wait_healthy=lambda timeout: True)
    recorder = SimpleNamespace(start=lambda: None)
    request = Request(
        {"type": "http", "app": SimpleNamespace(state=SimpleNamespace(media=media, rec=recorder))}
    )
    response = Response()

    result = onboarding.complete_onboarding(
        onboarding.CompleteOnboardingIn(
            label="http://yoosee.co/?D=0-7000000003-8034",
            mac=MAC,
            name="Test camera",
        ),
        request,
        response,
    )

    serialized = json.dumps(result)
    assert result["camera"]["id"] == camera.camera_id
    assert result["stages"][-1] == "registry"
    assert response.headers["cache-control"] == "no-store"
    assert DEVICE not in serialized
    assert MAC not in serialized
    assert "SafePass123" not in serialized


def test_completion_api_requires_printed_mac_before_p2p(monkeypatch):
    monkeypatch.setattr(
        onboarding,
        "inspect_label",
        lambda **_kwargs: {"device_id": DEVICE, "mac": "", "firmware_version": ""},
    )
    monkeypatch.setattr(
        onboarding,
        "bound_privileged_enrollment",
        lambda _device: (_ for _ in ()).throw(AssertionError("P2P opened")),
    )
    request = Request(
        {"type": "http", "app": SimpleNamespace(state=SimpleNamespace(media=None, rec=None))}
    )

    with pytest.raises(HTTPException) as caught:
        onboarding.complete_onboarding(
            onboarding.CompleteOnboardingIn(device_id=DEVICE, capability_code="8034"),
            request,
            Response(),
        )

    assert caught.value.status_code == 422


def test_completion_endpoint_uses_strict_local_guard():
    route = next(
        item
        for item in onboarding.router.routes
        if item.path == "/api/provisioning/privileged/complete"
    )
    dependencies = {dependency.call for dependency in route.dependant.dependencies}

    assert require_local_request in dependencies
