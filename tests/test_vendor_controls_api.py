from __future__ import annotations

from fastapi import Response

from backend.app.api import vendor_controls
from backend.app.api.local_only import require_local_request
from backend.app.camera_identity import stable_camera_id
from backend.app.db import registry
from backend.app.db.p2p import P2PEnrollment
from backend.app.vendor_p2p import (
    P2POrientationWrite,
    P2PWhiteLightState,
    P2PWhiteLightWrite,
)

CAMERA_ID = stable_camera_id("mac", "aa:bb:cc:dd:ee:02")


def _enrollment() -> P2PEnrollment:
    return P2PEnrollment(
        device_id="7000000002",
        access_id=123,
        access_token=bytes(range(64)),
        dev_token=None,
        created_at="now",
        updated_at="now",
    )


def test_white_light_read_returns_only_sanitized_typed_state(monkeypatch):
    enrollment = _enrollment()
    monkeypatch.setattr(registry, "get_camera_by_id", lambda _camera_id: object())
    observed = []
    monkeypatch.setattr(
        vendor_controls,
        "bound_privileged_enrollment_for_camera",
        lambda camera_id: observed.append(("enrollment", camera_id)) or enrollment,
    )
    monkeypatch.setattr(
        vendor_controls,
        "read_camera_white_light",
        lambda selected: observed.append(("read", selected.device_id))
        or P2PWhiteLightState(selected.device_id, False, True, True, True, True),
    )
    response = Response()

    result = vendor_controls.white_light_state(response, CAMERA_ID)

    assert observed == [
        ("enrollment", CAMERA_ID),
        ("read", "7000000002"),
    ]
    assert result == {
        "id": CAMERA_ID,
        "enabled": False,
        "authenticated": True,
        "direct_handshake": True,
        "transport_acknowledged": True,
        "application_acknowledged": True,
    }
    assert response.headers["cache-control"] == "no-store"


def test_white_light_write_passes_only_boolean_to_exact_enrollment(monkeypatch):
    enrollment = _enrollment()
    monkeypatch.setattr(registry, "get_camera_by_id", lambda _camera_id: object())
    observed = []
    monkeypatch.setattr(
        vendor_controls,
        "bound_privileged_enrollment_for_camera",
        lambda camera_id: observed.append(("enrollment", camera_id)) or enrollment,
    )

    def fake_write(selected, enabled):
        observed.append(("write", selected.device_id, enabled))
        return P2PWhiteLightWrite(selected.device_id, enabled, False, True, True, True, True)

    monkeypatch.setattr(vendor_controls, "set_camera_white_light", fake_write)

    result = vendor_controls.update_white_light(
        vendor_controls.WhiteLightIn(enabled=True),
        Response(),
        CAMERA_ID,
    )

    assert observed == [
        ("enrollment", CAMERA_ID),
        ("write", "7000000002", True),
    ]
    assert result["enabled"] is True
    assert result["previous_enabled"] is False
    assert result["verified"] is True


def test_vendor_control_routes_are_authenticated_and_lan_only():
    for route in vendor_controls.router.routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert vendor_controls.require_auth in dependencies
        assert require_local_request in dependencies


def test_orientation_write_uses_same_opaque_camera_association(monkeypatch):
    enrollment = _enrollment()
    monkeypatch.setattr(registry, "get_camera_by_id", lambda _camera_id: object())
    observed = []
    monkeypatch.setattr(
        vendor_controls,
        "bound_privileged_enrollment_for_camera",
        lambda camera_id: observed.append(("enrollment", camera_id)) or enrollment,
    )

    def fake_orientation(selected, orientation):
        observed.append(("orientation", selected.device_id, orientation))
        return P2POrientationWrite(
            selected.device_id, orientation, 1, 3, True, True, 0, True
        )

    monkeypatch.setattr(vendor_controls, "set_camera_orientation", fake_orientation)

    result = vendor_controls.update_orientation(
        vendor_controls.OrientationIn(orientation="inverted"),
        Response(),
        CAMERA_ID,
    )

    assert observed == [
        ("enrollment", CAMERA_ID),
        ("orientation", "7000000002", "inverted"),
    ]
    assert result["orientation"] == "inverted"
    assert result["verified"] is True
