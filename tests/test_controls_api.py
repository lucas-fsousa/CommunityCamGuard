from __future__ import annotations

import pytest
from fastapi import HTTPException, Response

from backend.app.api import controls
from backend.app.api.local_only import require_local_request
from backend.app.camera_identity import stable_camera_id
from backend.app.drivers import ControlNotReady, ControlOption, ControlResult, Unsupported
from backend.app.drivers.contracts import WeeklySchedule

CAMERA_ID = stable_camera_id("mac", "aa:bb:cc:dd:ee:03")


def test_generic_write_passes_only_semantic_key_and_value(monkeypatch):
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
        )

    monkeypatch.setattr(controls, "write_control", fake_write)
    response = Response()
    result = controls.write_camera_control(
        controls.ControlWriteIn(value=True),
        response,
        CAMERA_ID,
        "white_light",
    )

    assert observed == [(CAMERA_ID, "white_light", True)]
    assert result["control"] == "white_light"
    assert result["value"] is True
    assert result["verified"] is True
    assert "native_previous_value" not in result
    assert response.headers["cache-control"] == "no-store"


def test_weekly_schedule_is_strictly_parsed_before_driver_dispatch(monkeypatch):
    observed = []
    schedule = controls.ControlWriteIn.model_validate(
        {
            "value": {
                "start": "22:30",
                "end": "06:15",
                "weekdays": ["mon", "wed", "fri"],
            }
        }
    )

    def fake_write(camera_id, key, value):
        observed.append((camera_id, key, value))
        return ControlResult(key, value, previous_value=value, verified=True)

    monkeypatch.setattr(controls, "write_control", fake_write)
    result = controls.write_camera_control(
        schedule, Response(), CAMERA_ID, "smart_protection_schedule"
    )

    assert observed == [
        (
            CAMERA_ID,
            "smart_protection_schedule",
            WeeklySchedule("22:30", "06:15", ("mon", "wed", "fri")),
        )
    ]
    assert result["value"] == {
        "start": "22:30",
        "end": "06:15",
        "weekdays": ["mon", "wed", "fri"],
    }


@pytest.mark.parametrize(
    "value",
    [
        {"start": "24:00", "end": "06:00", "weekdays": ["mon"]},
        {"start": "22:00", "end": "6:00", "weekdays": ["mon"]},
        {"start": "22:00", "end": "06:00", "weekdays": []},
        {"start": "22:00", "end": "06:00", "weekdays": ["mon", "mon"]},
        {"start": "22:00", "end": "06:00", "weekdays": ["holiday"]},
    ],
)
def test_weekly_schedule_rejects_invalid_shape(value):
    with pytest.raises(ValueError):
        controls.ControlWriteIn.model_validate({"value": value})


def test_generic_read_returns_secret_free_projection(monkeypatch):
    monkeypatch.setattr(
        controls,
        "read_control",
        lambda camera_id, key: ControlResult(
            key,
            "inverted",
            authenticated=True,
            direct_connection=True,
            application_acknowledged=True,
            native_previous_value=3,
        ),
    )

    result = controls.read_camera_control(Response(), CAMERA_ID, "orientation")

    assert result["id"] == CAMERA_ID
    assert result["value"] == "inverted"
    assert result["direct_connection"] is True
    assert "native_previous_value" not in result


def test_generic_control_routes_are_authenticated_and_lan_only():
    for route in controls.router.routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        assert controls.require_auth in dependencies
        assert require_local_request in dependencies


def test_dynamic_options_return_only_the_generic_public_contract(monkeypatch):
    monkeypatch.setattr(
        controls,
        "control_options",
        lambda camera_id, key: (ControlOption("system-1", "Tone", "system", "1 s"),),
    )
    response = Response()

    result = controls.read_camera_control_options(response, CAMERA_ID, "alarm_voice")

    assert result == {
        "id": CAMERA_ID,
        "control": "alarm_voice",
        "options": [
            {"value": "system-1", "label": "Tone", "group": "system", "detail": "1 s"}
        ],
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (Unsupported("hidden"), 501),
        (ControlNotReady("driver material missing"), 409),
        (controls.ControlBusy("camera busy"), 409),
    ],
)
def test_generic_driver_failures_have_stable_http_semantics(monkeypatch, error, status):
    monkeypatch.setattr(
        controls,
        "write_control",
        lambda *_args: (_ for _ in ()).throw(error),
    )

    with pytest.raises(HTTPException) as caught:
        controls.write_camera_control(
            controls.ControlWriteIn(value=True),
            Response(),
            CAMERA_ID,
            "white_light",
        )

    assert caught.value.status_code == status
