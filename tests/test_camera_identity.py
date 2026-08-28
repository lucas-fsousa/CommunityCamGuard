from __future__ import annotations

import pytest

from backend.app.camera_identity import normalize_identity, stable_camera_id, valid_camera_id


def test_mac_variants_produce_the_same_opaque_public_id():
    colon = stable_camera_id("mac", "AA:BB:CC:DD:EE:FF")
    compact = stable_camera_id("mac", "aabbccddeeff")

    assert colon == compact
    assert valid_camera_id(colon) is True
    assert "aabbccddeeff" not in colon


def test_driver_identity_namespaces_do_not_collide():
    assert stable_camera_id("serial", "12345678") != stable_camera_id(
        "vendor_device", "12345678"
    )


@pytest.mark.parametrize(
    ("kind", "value"),
    [("ip", "192.0.2.1"), ("mac", "invalid"), ("serial", ""), ("serial", "bad\nvalue")],
)
def test_unstable_or_invalid_identity_material_is_rejected(kind, value):
    with pytest.raises(ValueError):
        normalize_identity(kind, value)
