import struct

import pytest

from backend.app.drivers.yoosee.p2p.onboard_playback_speed import (
    PLAYBACK_SPEED_COMMAND,
    PLAYBACK_SPEED_EXTENDED_SIZE,
    PLAYBACK_SPEED_LEGACY_SIZE,
    PLAYBACK_STRATEGY_COMMAND,
    build_playback_speed_request,
    unpack_playback_speed_request,
)


def test_playback_option_command_ids_match_native_sdk():
    assert (PLAYBACK_SPEED_COMMAND, PLAYBACK_STRATEGY_COMMAND) == (24, 25)


@pytest.mark.parametrize("rate", [1, 2.0, 4, 8.0])
def test_builds_legacy_speed_as_milli_rate(rate):
    payload = build_playback_speed_request(rate, device_platform_version=1)

    assert len(payload) == PLAYBACK_SPEED_LEGACY_SIZE
    assert payload == struct.pack("<I", int(rate * 1000))
    assert unpack_playback_speed_request(payload, device_platform_version=1) == (
        float(rate),
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("rate", "expected_fps", "drop_audio"),
    [(1.0, 0, False), (2.0, 0, False), (4.0, 4, True), (8.0, 4, True)],
)
def test_builds_platform_2_speed_with_sdk_default_media_policy(
    rate,
    expected_fps,
    drop_audio,
):
    payload = build_playback_speed_request(rate, device_platform_version=2)

    assert len(payload) == PLAYBACK_SPEED_EXTENDED_SIZE
    assert unpack_playback_speed_request(payload, device_platform_version=2) == (
        rate,
        expected_fps,
        drop_audio,
        False,
    )


@pytest.mark.parametrize("rate", [True, "2", 0, 0.5, 3, 16, float("nan")])
def test_speed_builder_rejects_unknown_rates(rate):
    with pytest.raises(ValueError):
        build_playback_speed_request(rate, device_platform_version=1)


@pytest.mark.parametrize("platform", [None, True, 0, 3])
def test_speed_codec_requires_authoritative_known_platform(platform):
    with pytest.raises(ValueError):
        build_playback_speed_request(1, device_platform_version=platform)
    with pytest.raises(ValueError):
        unpack_playback_speed_request(bytes(4), device_platform_version=platform)


def test_speed_unpacker_rejects_wrong_size_unknown_rate_and_reserved_flags():
    with pytest.raises(ValueError):
        unpack_playback_speed_request(bytes(5), device_platform_version=2)
    with pytest.raises(ValueError):
        unpack_playback_speed_request(struct.pack("<I", 3000), device_platform_version=1)
    with pytest.raises(ValueError):
        unpack_playback_speed_request(
            struct.pack("<IBB", 1000, 0, 0x80),
            device_platform_version=2,
        )
