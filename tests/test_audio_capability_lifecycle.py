"""Persisted audio proof/identity lifecycle, with no camera I/O or worker threads."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.db.registry import Camera
from backend.app.drivers.contracts import AudioMessageResult, ControlNotReady
from backend.app.drivers.yoosee import audio, capability_runtime
from backend.app.drivers.yoosee import audio_capabilities as policy
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_policy import OperationProof, ValidatedProfile
from backend.app.drivers.yoosee.capability_snapshot import CapabilitySnapshot


@pytest.mark.parametrize("streaming", [False, True])
def test_persisted_expiry_refresh_and_firmware_lifecycle(monkeypatch, streaming):
    camera = Camera(camera_id="cam_0123456789abcdef01234567", last_ip="192.0.2.10",
                    rtsp_port=554, stream_path="/onvif1")
    identity = CapabilityIdentity("1234", "5678", "test", 1, "1", "1", "")
    now = [1000.0]
    refreshes = []
    transmissions = []
    monkeypatch.setattr(policy.time, "time", lambda: now[0])
    monkeypatch.setattr(policy.p2p, "get_enrollment_for_camera",
                        lambda _: SimpleNamespace(device_id=identity.device_id))
    monkeypatch.setattr(capability_runtime, "request_refresh",
                        lambda *args: refreshes.append(args))
    profile = ValidatedProfile(camera.camera_id, identity, tuple(
        OperationProof(key, writable=True, options=frozenset({policy.RTSP}))
        for key in (policy.MESSAGE, policy.STREAM)))
    policy.profiles.register(profile, reviewed_at=900.0, sources={
        proof.key: "sha256:" + "a" * 64 + " synthetic lifecycle" for proof in profile.operations})
    policy.rollout.activate(profile)

    def publish(selected_identity, expires_at):
        generation = policy.snapshots.begin(camera.camera_id)
        assert policy.snapshots.save(CapabilitySnapshot(
            camera.camera_id, selected_identity, now[0], ()),
            generation=generation, expires_at=expires_at)

    def transport(_camera, payload):
        transmissions.append(payload)
        return AudioMessageResult(20, 1, 1, 1, True, True, True)

    monkeypatch.setattr(audio.rtsp_backchannel, "send", transport)
    monkeypatch.setattr(audio.rtsp_backchannel, "send_stream", transport)

    def send():
        if streaming:
            return audio.send_stream(camera, iter((bytes(640),)))
        return audio.send(camera, bytes(640))

    publish(identity, 1010.0)
    assert audio.supported(camera, streaming=streaming)
    assert send().completed
    assert len(transmissions) == 1 and not refreshes
    now[0] = 1010.0  # Exact expiry, not just some time after it.
    assert not audio.supported(camera, streaming=streaming)
    with pytest.raises(ControlNotReady):
        send()
    assert len(transmissions) == 1
    assert refreshes and all(args == (camera.camera_id, identity.device_id) for args in refreshes)

    publish(identity, 1020.0)
    assert audio.supported(camera, streaming=streaming)
    assert send().completed
    assert len(transmissions) == 2
    # Beginning a refresh invalidates the old identity even when collection fails.
    policy.snapshots.begin(camera.camera_id)
    with pytest.raises(ControlNotReady):
        send()
    assert len(transmissions) == 2
    publish(replace(identity, firmware="2"), 1020.0)
    assert not audio.supported(camera, streaming=streaming)
    with pytest.raises(ControlNotReady):
        send()
    assert len(transmissions) == 2  # Old firmware proofs cannot authorize the new one.
