import pytest

from backend.app.drivers.yoosee.p2p.video_definition import DefinitionRequest, encode_definitions


@pytest.mark.parametrize("value,wire", [(1, b"\x49\x12"), (2, b"\x92\x24"),
                                       (3, b"\xdb\x36"), (7, b"\xff\x7f")])
def test_platform_two_matches_sdk_five_slot_packing(value, wire):
    assert encode_definitions(2, dict.fromkeys(range(5), value)) == DefinitionRequest(0x33, wire)


def test_partial_channels_do_not_broadcast_and_order_is_irrelevant():
    assert encode_definitions(2, {0: 3}) == DefinitionRequest(0x33, b"\x03\x00")
    assert encode_definitions(2, {4: 3, 0: 1}) == encode_definitions(2, {0: 1, 4: 3})
    assert encode_definitions(2, {4: 3, 0: 1}).payload == b"\x01\x30"


@pytest.mark.parametrize("value,wire", [(1, b"\x00"), (2, b"\x01"), (3, b"\x02"), (7, b"\x06")])
def test_legacy_subtracts_one_and_uses_single_byte(value, wire):
    assert encode_definitions(1, {0: value}) == DefinitionRequest(5, wire)
    assert encode_definitions(1, dict.fromkeys(range(5), value)).payload == wire


@pytest.mark.parametrize("platform,definitions", [(None, {0: 3}), (0, {0: 3}), (3, {0: 3}),
    (True, {0: 3}), (2, {}), (2, {5: 3}), (2, {-1: 3}), (2, {True: 3}), (2, {"0": 3}),
    (2, {0: True}), (2, {0: 0}), (2, {0: 4}), (2, {0: "3"}), (1, {0: 1, 1: 3}),
    (2, dict.fromkeys(range(6), 3))])
def test_unknown_platform_or_ambiguous_or_unsupported_values_fail_closed(platform, definitions):
    with pytest.raises(ValueError):
        encode_definitions(platform, definitions)
