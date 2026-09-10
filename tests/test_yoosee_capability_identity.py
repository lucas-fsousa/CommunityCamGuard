from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.capability_identity import normalize_identity
from backend.app.drivers.yoosee.p2p.contracts import P2PPropertyRead

DEVICE = "7000000002"


def observations():
    return (
        P2PPropertyRead(
            DEVICE,
            "ProConst._productInfo",
            True,
            False,
            False,
            0,
            {"productID": "6442451494", "productModel": "GW-IPC-AK-AV100.25", "revision": 1},
        ),
        P2PPropertyRead(
            DEVICE,
            "ProConst._versionInfo",
            True,
            False,
            False,
            0,
            {"swVer": "40.1.22", "sdkVer": "16.20.16355", "hwVer": ""},
        ),
    )


def test_identity_preserves_distinct_profile_fields_without_requiring_transport_ack():
    product, version = observations()
    identity = normalize_identity(product, version, device_id=DEVICE)
    assert identity is not None
    assert (identity.product_id, identity.model, identity.revision) == (
        "6442451494",
        "GW-IPC-AK-AV100.25",
        1,
    )
    assert (identity.firmware, identity.sdk, identity.hardware) == ("40.1.22", "16.20.16355", "")


@pytest.mark.parametrize(
    "index,changes",
    [
        (0, {"device_id": "7000000003"}),
        (1, {"device_id": "7000000003"}),
        (0, {"property_path": "ProConst"}),
        (1, {"authenticated": False}),
        (0, {"error_code": None}),
        (1, {"error_code": 20001}),
        (0, {"error_code": False}),
        (0, {"value": None}),
        (1, {"value": {"setVal": {"swVer": "40.1.22"}, "t": 123}}),
    ],
)
def test_rejects_wrong_origin_and_incomplete_reads(index, changes):
    reads = list(observations())
    reads[index] = replace(reads[index], **changes)
    assert normalize_identity(*reads, device_id=DEVICE) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("productID", True),
        ("productID", 6442451494.0),
        ("productID", "06442451494"),
        ("productID", 1 << 64),
        ("productModel", ""),
        ("productModel", " IPC"),
        ("productModel", "IPC\n"),
        ("productModel", "x" * 129),
        ("revision", True),
        ("revision", -1),
    ],
)
def test_rejects_ambiguous_product_fields(field, value):
    product, version = observations()
    product = replace(product, value={**product.value, field: value})
    assert normalize_identity(product, version, device_id=DEVICE) is None


def test_integer_product_id_normalizes_without_collapsing_other_identity_fields():
    product, version = observations()
    expected = normalize_identity(product, version, device_id=DEVICE)
    product = replace(product, value={**product.value, "productID": 6442451494})
    assert normalize_identity(product, version, device_id=DEVICE) == expected
    for field in ("hwVer", "swVer", "sdkVer"):
        changed = replace(version, value={**version.value, field: "different"})
        assert normalize_identity(product, changed, device_id=DEVICE) != expected
        missing = replace(version, value={k: v for k, v in version.value.items() if k != field})
        assert normalize_identity(product, missing, device_id=DEVICE) is None
