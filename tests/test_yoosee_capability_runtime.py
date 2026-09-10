from types import SimpleNamespace

from backend.app.drivers.yoosee import capability_profiles as profiles
from backend.app.drivers.yoosee import capability_rollout as rollout
from backend.app.drivers.yoosee import capability_runtime as runtime
from backend.app.drivers.yoosee import capability_snapshot_store as snapshots
from backend.app.drivers.yoosee import controls
from backend.app.drivers.yoosee.capability_evidence import EvidenceState as State
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_policy import OperationProof, ValidatedProfile
from backend.app.drivers.yoosee.capability_snapshot import CapabilitySnapshot, PropertyEvidence

CAMERA = "cam_" + "1" * 24
IDENTITY = CapabilityIdentity("7000000002", "123", "model", 1, "1", "2", "")
PROFILE = ValidatedProfile(
    CAMERA,
    IDENTITY,
    (OperationProof("night_vision", writable=True, options=frozenset({"automatic", "daytime"})),),
)


def test_runtime_migrates_only_selected_unit_and_controls(monkeypatch):
    monkeypatch.setattr(controls.p2p, "has_enrollment_for_camera", lambda _: True)
    monkeypatch.setattr(
        controls.p2p,
        "get_enrollment_for_camera",
        lambda _: SimpleNamespace(device_id=IDENTITY.device_id),
    )
    monkeypatch.setattr(controls.time, "time", lambda: 150)
    requested = []
    monkeypatch.setattr(runtime, "request_refresh", lambda *args: requested.append(args))
    camera = SimpleNamespace(camera_id=CAMERA)
    legacy = controls.catalog(camera)
    assert requested == []
    profiles.register(
        PROFILE, sources={"night_vision": "sha256:" + "a" * 64 + " proof#night"}, reviewed_at=100
    )
    rollout.activate(PROFILE)
    assert "night_vision" not in {item.key for item in controls.catalog(camera)}
    snapshots.save(
        CapabilitySnapshot(
            CAMERA, IDENTITY, 100, (PropertyEvidence("night_vision", State.SUPPORTED, 1),)
        ),
        generation=snapshots.begin(CAMERA),
        expires_at=200,
    )
    current = {item.key: item for item in controls.catalog(camera)}
    assert current["night_vision"].options == ("automatic", "daytime")
    assert all(current[item.key] == item for item in legacy if item.key != "night_vision")
    assert controls.catalog(SimpleNamespace(camera_id="cam_" + "2" * 24)) == legacy
    assert requested and all(args == (CAMERA, IDENTITY.device_id) for args in requested)


def test_refresh_is_bounded_single_worker_with_backoff(monkeypatch):
    monkeypatch.setattr(runtime, "_busy", False)
    monkeypatch.setattr(runtime, "_attempts", {})
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 1000)
    tasks = []

    class Thread:
        def __init__(self, *, target, name, daemon):
            assert daemon
            self.target = target

        def start(self):
            tasks.append(self.target)

    monkeypatch.setattr(runtime.threading, "Thread", Thread)
    monkeypatch.setattr(runtime.p2p, "get_enrollment_for_camera", lambda _: None)
    runtime.request_refresh(CAMERA, IDENTITY.device_id)
    runtime.request_refresh("other", IDENTITY.device_id)
    assert len(tasks) == 1
    tasks[0]()
    assert not runtime._busy
    runtime.request_refresh(CAMERA, IDENTITY.device_id)
    assert len(tasks) == 1
