"""Siren migration wiring without sockets or real actuation."""

from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee import capability_refresh as refresh
from backend.app.drivers.yoosee import capability_rollout as rollout
from backend.app.drivers.yoosee import capability_runtime as runtime
from backend.app.drivers.yoosee import controls
from backend.app.drivers.yoosee.capability_evidence import EvidenceState
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_policy import (
    OperationProof,
    ValidatedProfile,
    select_controls,
)
from backend.app.drivers.yoosee.p2p.siren import P2PSirenPulse


@pytest.mark.parametrize("selected_controls", [None, frozenset({"white_light"}), frozenset({"siren_pulse"})])
def test_pulse_dispatch_uses_strict_transport_only_for_selected_control(monkeypatch, selected_controls):
    calls = []
    monkeypatch.setattr(rollout, "selected", lambda _: (
        None if selected_controls is None else (None, selected_controls)
    ))
    monkeypatch.setattr(controls, "_enrollment", lambda _: object())
    monkeypatch.setattr(controls, "run_with_fresh_access", lambda access, fn: fn(access))

    def pulse(_access, duration, **kwargs):
        calls.append((duration, kwargs))
        return P2PSirenPulse("7000000002", duration, True, 0, True, 0, True)

    monkeypatch.setattr(controls, "pulse_camera_siren", pulse)
    result = controls.write(SimpleNamespace(camera_id="test"), "siren_pulse", 2)
    assert result.verified and result.application_acknowledged
    strict = selected_controls is not None and "siren_pulse" in selected_controls
    assert calls == [(2, {"require_correlated_response": True} if strict else {})]


@pytest.mark.parametrize("mode", ["absent", "other_control", "wrong_device", "siren"])
def test_runtime_collection_opt_in_is_rechecked_for_exact_device(monkeypatch, mode):
    tasks, calls = [], []
    monkeypatch.setattr(runtime, "_busy", False)
    monkeypatch.setattr(runtime, "_attempts", {})
    enrollment = SimpleNamespace(device_id="7000000002")
    monkeypatch.setattr(runtime.p2p, "get_enrollment_for_camera", lambda _: enrollment)

    class Thread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            tasks.append(self.target)

    monkeypatch.setattr(runtime.threading, "Thread", Thread)
    monkeypatch.setattr(runtime, "refresh", lambda access, **kw: calls.append((access, kw)))
    identity = SimpleNamespace(device_id="other" if mode == "wrong_device" else enrollment.device_id)
    selected = None if mode == "absent" else (
        identity, frozenset({"white_light" if mode == "other_control" else "siren_pulse"}),
    )
    monkeypatch.setattr(runtime, "selected", lambda _: selected)
    runtime.request_refresh("test", enrollment.device_id)
    assert len(tasks) == 1 and not calls
    tasks[0]()
    assert calls == [(enrollment, {
        "validity_seconds": runtime.VALIDITY_SECONDS, "include_siren": mode == "siren",
    })]
    assert not runtime._busy


def test_refresh_forwards_optional_siren_without_new_session_or_save_on_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(refresh.store, "begin", lambda _: 1)
    monkeypatch.setattr(refresh, "collect_snapshot", lambda access, **kw: calls.append(kw))
    monkeypatch.setattr(refresh.store, "save", lambda *a, **kw: pytest.fail("saved missing snapshot"))
    assert not refresh.refresh(SimpleNamespace(camera_id="test"), validity_seconds=60, include_siren=True)
    assert calls == [{"include_siren": True}]


@pytest.mark.parametrize("state", list(EvidenceState))
def test_two_second_proof_never_grants_other_durations_or_unknown_state(state):
    identity = CapabilityIdentity("7000000002", "123", "model", 1, "1", "2", "")
    profile = ValidatedProfile("test", identity, (
        OperationProof("siren_pulse", writable=True, options=frozenset({"2"})),
    ))
    selected = select_controls(
        controls._DESCRIPTORS, camera_id="test", identity=identity, profile=profile,
        evidence={"siren_pulse": state},
    )
    if state == EvidenceState.SUPPORTED:
        assert len(selected) == 1
        assert selected[0].key == "siren_pulse" and selected[0].options == ("2",)
        assert selected[0].writable and not selected[0].readable
    else:
        assert selected == ()
