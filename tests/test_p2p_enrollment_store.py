from __future__ import annotations

import sqlite3

import pytest

from backend.app.camera_identity import stable_camera_id
from backend.app.config import get_settings
from backend.app.db import p2p

DEVICE_ID = "7000000001"
ACCESS_TOKEN = bytes(range(64))
DEV_TOKEN = "ab" * 64
CAMERA_ID = stable_camera_id("mac", "aa:bb:cc:dd:ee:01")


def test_enrollment_round_trip_is_encrypted_at_rest():
    saved = p2p.upsert_enrollment(
        DEVICE_ID,
        access_id=0xFEDCBA9876543210,
        access_token=ACCESS_TOKEN,
        dev_token=DEV_TOKEN,
    )
    loaded = p2p.get_enrollment(DEVICE_ID)

    assert loaded == saved
    assert p2p.has_enrollment(DEVICE_ID) is True

    with sqlite3.connect(get_settings().db_path) as conn:
        blob = bytes(conn.execute(
            "SELECT secret_enc FROM p2p_enrollments WHERE device_id = ?", (DEVICE_ID,)
        ).fetchone()[0])
    assert DEV_TOKEN.encode() not in blob
    assert ACCESS_TOKEN.hex().encode() not in blob
    assert str(saved.access_id).encode() not in blob


def test_enrollment_upsert_preserves_created_at_and_rotates_secrets():
    first = p2p.upsert_enrollment(
        DEVICE_ID,
        access_id=1,
        access_token=ACCESS_TOKEN,
        dev_token=DEV_TOKEN,
        camera_id=CAMERA_ID,
    )
    second_token = "cd" * 64
    second = p2p.upsert_enrollment(
        DEVICE_ID, access_id=2, access_token=bytes(reversed(ACCESS_TOKEN)), dev_token=second_token
    )

    assert second.created_at == first.created_at
    assert p2p.get_enrollment(DEVICE_ID).dev_token == second_token
    assert second.camera_id == CAMERA_ID
    assert p2p.get_enrollment_for_camera(CAMERA_ID) == second
    assert p2p.has_enrollment_for_camera(CAMERA_ID) is True


def test_access_only_material_supports_read_only_probe_without_claiming_subscription():
    saved = p2p.upsert_enrollment(
        DEVICE_ID, access_id=1, access_token=ACCESS_TOKEN
    )

    assert saved.dev_token is None
    assert p2p.get_enrollment(DEVICE_ID).dev_token is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("device_id", "camera-three", "device ID"),
        ("access_id", -1, "access ID"),
        ("access_token", b"short", "access token"),
        ("dev_token", "not-hex", "subscription token"),
        ("camera_id", "not-an-id", "camera ID"),
    ],
)
def test_invalid_enrollment_material_is_rejected(field, value, message):
    values = {
        "device_id": DEVICE_ID,
        "access_id": 1,
        "access_token": ACCESS_TOKEN,
        "dev_token": DEV_TOKEN,
        "camera_id": None,
    }
    values[field] = value
    with pytest.raises(ValueError, match=message):
        p2p.upsert_enrollment(**values)


def test_delete_enrollment_removes_only_selected_device():
    p2p.upsert_enrollment(
        DEVICE_ID, access_id=1, access_token=ACCESS_TOKEN, dev_token=DEV_TOKEN
    )
    p2p.upsert_enrollment(
        "7000000002", access_id=2, access_token=ACCESS_TOKEN, dev_token="cd" * 64
    )

    p2p.delete_enrollment(DEVICE_ID)

    assert p2p.get_enrollment(DEVICE_ID) is None
    assert p2p.has_enrollment("7000000002") is True


def test_late_camera_link_is_one_to_one():
    p2p.upsert_enrollment(
        DEVICE_ID, access_id=1, access_token=ACCESS_TOKEN, dev_token=DEV_TOKEN
    )
    p2p.upsert_enrollment(
        "7000000002", access_id=2, access_token=ACCESS_TOKEN, dev_token="cd" * 64
    )

    linked = p2p.link_enrollment_to_camera(DEVICE_ID, CAMERA_ID)

    assert linked.camera_id == CAMERA_ID
    assert p2p.get_enrollment_for_camera(CAMERA_ID) == linked
    with pytest.raises(ValueError, match="already linked"):
        p2p.link_enrollment_to_camera("7000000002", CAMERA_ID)
