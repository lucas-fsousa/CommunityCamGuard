import struct

import pytest

from backend.app.drivers.yoosee.p2p.platform_metadata import (
    PUSH_STREAM_DISTRIBUTE_TYPE,
    DevicePlatformMetadata,
    parse_push_stream_platform_metadata,
)


def _distribution(
    *,
    device_id: int = 7_443_576_841,
    new_platform: bool = True,
    token: bytes = b"token",
    v4_relays: int = 1,
    v6_relays: int = 1,
) -> bytes:
    length = 0x88 + len(token) + 2 + v4_relays * 16 + v6_relays * 28
    frame = bytearray(length)
    frame[:2] = bytes((0x7E, PUSH_STREAM_DISTRIBUTE_TYPE))
    struct.pack_into("<H", frame, 2, length)
    frame[0x18] = int(new_platform)
    struct.pack_into("<H", frame, 0x1E, len(token))
    struct.pack_into("<Q", frame, 0x38, device_id)
    frame[0x88 : 0x88 + len(token)] = token
    relay_offset = 0x88 + len(token)
    frame[relay_offset] = v4_relays
    frame[relay_offset + 1] = v6_relays
    return bytes(frame)


@pytest.mark.parametrize(
    ("new_platform", "expected_version"),
    [(False, 1), (True, 2)],
)
def test_decodes_correlated_authoritative_platform_version(
    new_platform,
    expected_version,
):
    frame = _distribution(new_platform=new_platform)

    assert parse_push_stream_platform_metadata(
        frame,
        expected_device_id=7_443_576_841,
    ) == DevicePlatformMetadata(7_443_576_841, expected_version, new_platform)


def test_rejects_wrong_device_type_length_ack_and_truncated_relay_table():
    frame = bytearray(_distribution())
    assert (
        parse_push_stream_platform_metadata(bytes(frame), expected_device_id=7_443_576_842)
        is None
    )

    frame[1] = 0xE3
    assert (
        parse_push_stream_platform_metadata(bytes(frame), expected_device_id=7_443_576_841)
        is None
    )
    frame = bytearray(_distribution())
    struct.pack_into("<H", frame, 2, len(frame) - 1)
    assert (
        parse_push_stream_platform_metadata(bytes(frame), expected_device_id=7_443_576_841)
        is None
    )
    frame = bytearray(_distribution())
    struct.pack_into("<I", frame, 0x14, 1 << 20)
    assert (
        parse_push_stream_platform_metadata(bytes(frame), expected_device_id=7_443_576_841)
        is None
    )
    frame = _distribution()[:-1]
    assert parse_push_stream_platform_metadata(frame, expected_device_id=7_443_576_841) is None


def test_accepts_bounded_future_trailing_distribution_fields():
    frame = bytearray(_distribution())
    frame.extend(bytes(8))
    struct.pack_into("<H", frame, 2, len(frame))

    parsed = parse_push_stream_platform_metadata(
        bytes(frame),
        expected_device_id=7_443_576_841,
    )

    assert parsed is not None and parsed.version == 2


@pytest.mark.parametrize("device_id", [None, True, 0, -1, 0x10000000000000000])
def test_expected_device_id_must_be_a_positive_u64(device_id):
    with pytest.raises(ValueError):
        parse_push_stream_platform_metadata(_distribution(), expected_device_id=device_id)
