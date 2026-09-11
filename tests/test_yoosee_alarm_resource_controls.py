"""Fresh resource proofs guard both enumeration and selection without camera I/O."""
import base64
import struct
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee import alarm_resource_controls as controls
from backend.app.drivers.yoosee.capability_evidence import EvidenceState, alarm_resource_evidence
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_resources import (
    ResourceProfile,
    ResourceSelectionProof,
    resource_identity_digest,
)
from backend.app.drivers.yoosee.p2p.alarm_voice import AlarmVoiceResource
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError


def resource(number=7, opaque=10):
    native = base64.b64encode(struct.pack("<IIQQ", 4, number, 0, opaque)).decode()
    return AlarmVoiceResource(f"system-{number}", "Sound", 1000, "AMR", True, number, native)


IDENTITY = CapabilityIdentity("7000000002", "123", "model", 1, "firmware", "sdk", "")
ITEM = resource()
PROFILE = ResourceProfile("cam_" + "1" * 24, IDENTITY, True, (
    ResourceSelectionProof(ITEM.key, resource_identity_digest(ITEM)),
))
ACCESS = SimpleNamespace(camera_id=PROFILE.camera_id, device_id=IDENTITY.device_id)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(controls.rollout, "selected", lambda _: (IDENTITY, frozenset({"alarm_voice"})))
    monkeypatch.setattr(controls.snapshots, "resolve", lambda **_: EvidenceState.SUPPORTED)
    monkeypatch.setattr(controls.profiles, "load", lambda **_: PROFILE)
    monkeypatch.setattr(controls, "run_with_fresh_access", lambda access, fn: fn(access))
    calls = []

    def catalogue(access, **kwargs):
        assert kwargs == {"require_correlated_response": True}
        calls.append("catalogue")
        return SimpleNamespace(device_id=access.device_id, resources=(ITEM, resource(8)))

    def write(access, item, **kwargs):
        assert item == ITEM
        assert kwargs == {"require_exact_resource": True}
        calls.append("write")
        return "selected"

    monkeypatch.setattr(controls, "read_camera_alarm_voice_catalog", catalogue)
    monkeypatch.setattr(controls, "set_camera_alarm_voice_resource", write)
    return calls


def test_listing_and_selection_each_resolve_a_fresh_certified_catalogue(setup):
    assert controls.options(ACCESS) == (ITEM,)
    assert controls.select(ACCESS, ITEM.key) == "selected"
    assert setup == ["catalogue", "catalogue", "write"]


def test_unproven_key_never_reaches_write(setup):
    with pytest.raises(P2PProbeError, match="not certified"):
        controls.select(ACCESS, "system-8")
    assert setup == ["catalogue"]


def test_replaced_native_identity_never_inherits_slot_proof(setup, monkeypatch):
    monkeypatch.setattr(controls, "read_camera_alarm_voice_catalog", lambda *a, **k:
                        SimpleNamespace(device_id=IDENTITY.device_id, resources=(resource(opaque=99),)))
    assert controls.options(ACCESS) == ()
    with pytest.raises(P2PProbeError, match="replaced"):
        controls.select(ACCESS, ITEM.key)
    assert "write" not in setup


@pytest.mark.parametrize("reason", ["revoked", "unknown", "not_migrated", "wrong_device"])
def test_untrusted_context_stops_before_network(setup, monkeypatch, reason):
    if reason == "revoked":
        monkeypatch.setattr(controls.profiles, "load", lambda **_: None)
    elif reason == "unknown":
        monkeypatch.setattr(controls.snapshots, "resolve", lambda **_: EvidenceState.UNKNOWN)
    else:
        selected = None if reason == "not_migrated" else (replace(IDENTITY, device_id="other"), {"alarm_voice"})
        monkeypatch.setattr(controls.rollout, "selected", lambda _: selected)
    with pytest.raises(P2PProbeError):
        controls.select(ACCESS, ITEM.key)
    assert setup == []


def test_revocation_during_enumeration_prevents_write(setup, monkeypatch):
    profiles = iter((PROFILE, None))
    monkeypatch.setattr(controls.profiles, "load", lambda **_: next(profiles))
    with pytest.raises(P2PProbeError, match="proofs"):
        controls.select(ACCESS, ITEM.key)
    assert setup == ["catalogue"]


def test_catalogue_target_must_match(setup, monkeypatch):
    monkeypatch.setattr(controls, "read_camera_alarm_voice_catalog", lambda *a, **k:
                        SimpleNamespace(device_id="other", resources=(ITEM,)))
    with pytest.raises(P2PProbeError, match="device mismatch"):
        controls.options(ACCESS)


@pytest.mark.parametrize("value,expected", [
    (None, EvidenceState.UNKNOWN),
    ({"t": -1}, EvidenceState.UNSUPPORTED),
    ({"t": 0, "setVal": {"supportFunc": 1, "resId": ITEM.resource_id}}, EvidenceState.UNKNOWN),
    ({"t": 12, "setVal": {"supportFunc": 0}}, EvidenceState.UNSUPPORTED),
    ({"t": 12, "setVal": {"supportFunc": True, "resId": ITEM.resource_id}}, EvidenceState.UNKNOWN),
    ({"t": 12, "setVal": {"supportFunc": 1, "resId": "invalid"}}, EvidenceState.UNKNOWN),
    ({"t": 12, "setVal": {"supportFunc": 1, "resId": ITEM.resource_id}}, EvidenceState.SUPPORTED),
])
def test_resource_evidence_requires_explicit_supported_structure(value, expected):
    assert alarm_resource_evidence(value) == expected
