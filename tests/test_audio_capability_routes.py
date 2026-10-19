from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.db.registry import Camera
from backend.app.drivers.contracts import ControlNotReady
from backend.app.drivers.yoosee import audio
from backend.app.drivers.yoosee import audio_capabilities as policy
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_policy import OperationProof, ValidatedProfile

CAMERA = Camera(camera_id="cam_0123456789abcdef01234567", last_ip="192.0.2.10",
                rtsp_port=554, stream_path="/onvif1")
IDENTITY = CapabilityIdentity("1234", "5678", "test", 1, "1", "1", "")


@pytest.fixture
def managed(monkeypatch):
    profile = ValidatedProfile(CAMERA.camera_id, IDENTITY, (
        OperationProof(policy.MESSAGE, writable=True, options=frozenset({policy.RTSP})),
    ))
    monkeypatch.setattr(policy.rollout, "selected", lambda _: (IDENTITY, {policy.MESSAGE}))
    monkeypatch.setattr(policy.p2p, "get_enrollment_for_camera",
                        lambda _: SimpleNamespace(device_id=IDENTITY.device_id))
    monkeypatch.setattr(policy.snapshots, "is_current", lambda **_: True)
    monkeypatch.setattr(policy.profiles, "load", lambda **_: profile)
    return profile


def test_exact_message_proof_does_not_authorize_stream_or_p2p(managed):
    assert policy.allowed_routes(CAMERA, policy.MESSAGE) == {policy.RTSP}
    assert policy.allowed_routes(CAMERA, policy.STREAM) == set()
    assert audio.supported(CAMERA)
    assert not audio.supported(CAMERA, streaming=True)


@pytest.mark.parametrize("kind", ["absent", "other_unit", "firmware", "readonly", "duplicate", "unknown_route"])
def test_invalid_or_unproven_profile_denies_route(monkeypatch, managed, kind):
    profile = managed
    if kind == "absent":
        profile = None
    elif kind == "other_unit":
        profile = replace(profile, camera_id="cam_ffffffffffffffffffffffff")
    elif kind == "firmware":
        profile = replace(profile, identity=replace(IDENTITY, firmware="2"))
    elif kind == "readonly":
        profile = replace(profile, operations=(OperationProof(policy.MESSAGE, readable=True),))
    elif kind == "duplicate":
        profile = replace(profile, operations=profile.operations * 2)
    else:
        profile = replace(profile, operations=(OperationProof(
            policy.MESSAGE, writable=True, options=frozenset({"unknown"})),))
    monkeypatch.setattr(policy.profiles, "load", lambda **_: profile)
    assert policy.allowed_routes(CAMERA, policy.MESSAGE) == set()


@pytest.mark.parametrize("enrollment", [None, SimpleNamespace(device_id="other")])
def test_identity_association_required(monkeypatch, managed, enrollment):
    monkeypatch.setattr(policy.p2p, "get_enrollment_for_camera", lambda _: enrollment)
    assert policy.allowed_routes(CAMERA, policy.MESSAGE) == set()


def test_expired_snapshot_denies_and_requests_bounded_refresh(monkeypatch, managed):
    from backend.app.drivers.yoosee import capability_runtime
    calls = []
    monkeypatch.setattr(policy.snapshots, "is_current", lambda **_: False)
    monkeypatch.setattr(capability_runtime, "request_refresh", lambda *args: calls.append(args))
    assert policy.allowed_routes(CAMERA, policy.MESSAGE) == set()
    assert calls == [(CAMERA.camera_id, IDENTITY.device_id)]


def test_missing_rtsp_coordinates_does_not_fall_back_to_p2p(managed):
    camera = replace(CAMERA, last_ip="")
    assert policy.allowed_routes(camera, policy.MESSAGE) == set()
    with pytest.raises(ControlNotReady):
        audio.send(camera, bytes(640))


def test_stream_denial_does_not_consume_microphone_iterator(managed):
    def chunks():
        raise AssertionError("microphone must not be consumed")
        yield b""
    with pytest.raises(ControlNotReady):
        audio.send_stream(CAMERA, chunks())


def test_transport_failure_never_replays_message_via_p2p(monkeypatch, managed):
    from backend.app.drivers.contracts import ControlOperationError
    def fail(*_):
        raise OSError("simulated")
    monkeypatch.setattr(audio.rtsp_backchannel, "send", fail)
    monkeypatch.setattr(audio, "send_pcm_intercom", lambda *_: pytest.fail("unproven fallback"))
    with pytest.raises(ControlOperationError):
        audio.send(CAMERA, bytes(640))


def test_non_audio_rollout_preserves_legacy_gate(monkeypatch):
    monkeypatch.setattr(policy.rollout, "selected", lambda _: (IDENTITY, {"orientation"}))
    assert policy.allowed_routes(CAMERA, policy.MESSAGE) is None
    assert audio.supported(CAMERA)


def test_explicit_p2p_proof_never_selects_rtsp(monkeypatch, managed):
    profile = replace(managed, operations=(OperationProof(
        policy.MESSAGE, writable=True, options=frozenset({policy.P2P})),))
    monkeypatch.setattr(policy.profiles, "load", lambda **_: profile)
    assert policy.allowed_routes(CAMERA, policy.MESSAGE) == {policy.P2P}
    assert audio._use_rtsp(CAMERA, policy.MESSAGE) is False


def test_persisted_profile_and_explicit_rollout_intersect(monkeypatch):
    profile = ValidatedProfile(CAMERA.camera_id, IDENTITY, (
        OperationProof(policy.MESSAGE, writable=True, options=frozenset({policy.RTSP})),
    ))
    monkeypatch.setattr(policy.time, "time", lambda: 1000.0)
    monkeypatch.setattr(policy.p2p, "get_enrollment_for_camera",
                        lambda _: SimpleNamespace(device_id=IDENTITY.device_id))
    monkeypatch.setattr(policy.snapshots, "is_current", lambda **_: True)
    policy.rollout.activate(profile)
    assert policy.allowed_routes(CAMERA, policy.MESSAGE) == set()
    assert policy.profiles.register(
        profile, sources={policy.MESSAGE: "sha256:" + "a" * 64 + " synthetic test"},
        reviewed_at=900.0,
    )
    assert policy.allowed_routes(CAMERA, policy.MESSAGE) == {policy.RTSP}
    assert not audio.supported(CAMERA, streaming=True)
    assert policy.profiles.register(
        replace(profile, identity=replace(IDENTITY, firmware="new")),
        sources={policy.MESSAGE: "sha256:" + "b" * 64 + " synthetic replacement"},
        reviewed_at=950.0,
    )
    assert policy.allowed_routes(CAMERA, policy.MESSAGE) == set()
