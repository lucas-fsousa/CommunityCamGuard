from __future__ import annotations

import pytest
from fastapi import HTTPException, Response

from backend.app.api import vendor_controls
from backend.app.api.local_only import require_local_request
from backend.app.camera_identity import stable_camera_id
from backend.app.drivers import ControlNotReady, ControlOperationError, ControlResult, Unsupported
from backend.app.services import CameraNotFound

CAMERA_ID = stable_camera_id("mac", "aa:bb:cc:dd:ee:02")


def test_white_light_read_returns_only_sanitized_driver_state(monkeypatch):
    observed = []

    def fake_read(camera_id, key):
        observed.append((camera_id, key))
        return ControlResult(
            key,
            False,
            verified=True,
            authenticated=True,
            direct_connection=True,
            transport_acknowledged=True,
            application_acknowledged=True,
        )

    monkeypatch.setattr(vendor_controls, "read_control", fake_read)
    response = Response()

    result = vendor_controls.white_light_state(response, CAMERA_ID)

    assert observed == [(CAMERA_ID, "white_light")]
    assert result == {
        "id": CAMERA_ID,
        "enabled": False,
        "authenticated": True,
        "direct_handshake": True,
        "transport_acknowledged": True,
        "application_acknowledged": True,
    }
    assert response.headers["cache-control"] == "no-store"


def test_white_light_write_passes_only_semantic_boolean(monkeypatch):
    observed = []

    def fake_write(camera_id, key, value):
        observed.append((camera_id, key, value))
        return ControlResult(
            key,
            value,
            previous_value=False,
            changed=True,
            verified=True,
            transport_acknowledged=True,
            application_acknowledged=True,
        )

    monkeypatch.setattr(vendor_controls, "write_control", fake_write)

    result = vendor_controls.update_white_light(
        vendor_controls.WhiteLightIn(enabled=True),
        Response(),
        CAMERA_ID,
    )

    assert observed == [(CAMERA_ID, "white_light", True)]
    assert result["enabled"] is True
    assert result["previous_enabled"] is False
    assert result["verified"] is True


def test_vendor_control_routes_are_authenticated_and_lan_only():
    for route in vendor_controls.router.routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert vendor_controls.require_auth in dependencies
        assert require_local_request in dependencies


def test_orientation_write_uses_driver_dispatch(monkeypatch):
    observed = []

    def fake_write(camera_id, key, value):
        observed.append((camera_id, key, value))
        return ControlResult(
            key,
            value,
            changed=True,
            verified=True,
            transport_acknowledged=True,
            error_code=0,
            native_previous_value=1,
            native_requested_value=3,
        )

    monkeypatch.setattr(vendor_controls, "write_control", fake_write)

    result = vendor_controls.update_orientation(
        vendor_controls.OrientationIn(orientation="inverted"),
        Response(),
        CAMERA_ID,
    )

    assert observed == [(CAMERA_ID, "orientation", "inverted")]
    assert result["orientation"] == "inverted"
    assert result["previous_value"] == 1
    assert result["requested_value"] == 3
    assert result["verified"] is True


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (CameraNotFound("camera not found"), 404),
        (Unsupported("orientation"), 501),
        (ControlNotReady("driver material missing"), 409),
        (ControlOperationError("camera transport failed"), 502),
    ],
)
def test_driver_failures_have_stable_http_semantics(monkeypatch, error, status):
    monkeypatch.setattr(
        vendor_controls,
        "write_control",
        lambda *_args: (_ for _ in ()).throw(error),
    )

    with pytest.raises(HTTPException) as caught:
        vendor_controls.update_orientation(
            vendor_controls.OrientationIn(orientation="normal"),
            Response(),
            CAMERA_ID,
        )

    assert caught.value.status_code == status
