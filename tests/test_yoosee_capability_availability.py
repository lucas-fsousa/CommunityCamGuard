from types import SimpleNamespace

import pytest

from backend.app.drivers.base import Unsupported
from backend.app.drivers.contracts import ControlNotReady
from backend.app.drivers.yoosee import capability_availability as availability
from backend.app.drivers.yoosee.capability_evidence import EvidenceState
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity

IDENTITY = CapabilityIdentity("123", "456", "model", 1, "firmware", "sdk", "")


@pytest.mark.parametrize("state,managed,enrolled,error", [
    (EvidenceState.UNKNOWN, True, True, ControlNotReady),
    (EvidenceState.UNSUPPORTED, True, True, Unsupported),
    (EvidenceState.SUPPORTED, True, True, Unsupported),  # missing proof is not a transient grant
    (EvidenceState.UNKNOWN, False, True, Unsupported),
    (EvidenceState.UNKNOWN, True, False, Unsupported),
])
def test_missing_control_reason_is_exact_unit_and_evidence_specific(monkeypatch, state, managed, enrolled, error):
    monkeypatch.setattr(availability.rollout, "selected", lambda _:
                        (IDENTITY, {"alarm_voice"}) if managed else None)
    monkeypatch.setattr(availability.p2p, "get_enrollment_for_camera", lambda _:
                        SimpleNamespace(device_id=IDENTITY.device_id if enrolled else "other"))
    monkeypatch.setattr(availability.snapshots, "resolve", lambda **_: state)
    with pytest.raises(error):
        availability.unavailable_control(SimpleNamespace(camera_id="cam_" + "1" * 24), "alarm_voice")
