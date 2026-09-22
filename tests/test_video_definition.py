import pytest

from backend.app.drivers.yoosee.p2p.video_definition import (
    DefinitionRequest,
    encode_definitions,
    with_startup_definitions,
)


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
    with pytest.raises(ValueError):
        with_startup_definitions(bytes(32), platform, definitions)


@pytest.mark.parametrize("platform,offset,length", [(1, 0, 1), (2, 23, 2)])
@pytest.mark.parametrize("definition", [1, 2, 3, 7])
def test_startup_changes_only_sdk_quality_field(platform, offset, length, definition):
    original = bytes(range(32))
    definitions = dict.fromkeys(range(5), definition)
    result = with_startup_definitions(original, platform, definitions)
    assert isinstance(result, bytes)
    assert len(result) == 32
    assert result[:offset] == original[:offset]
    assert result[offset:offset + length] == encode_definitions(platform, definitions).payload
    assert result[offset + length:] == original[offset + length:]
    assert original == bytes(range(32))
    assert with_startup_definitions(result, platform, definitions) == result


@pytest.mark.parametrize("template", [None, "x" * 32, bytearray(32), bytes(31), bytes(33)])
def test_startup_rejects_missing_mutable_or_wrong_size_template(template):
    with pytest.raises(ValueError):
        with_startup_definitions(template, 2, {0: 3})


def test_startup_sparse_hd_does_not_invent_other_slot_values():
    original = bytes(range(32))
    result = with_startup_definitions(original, 2, {0: 3})
    assert result[23:25] == b"\x03\x00"
    assert result[0] == original[0]
