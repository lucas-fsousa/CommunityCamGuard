import pytest

from backend.app.drivers.yoosee.p2p.media_protocol import build_av_init
from backend.app.drivers.yoosee.p2p.video_definition import encode_definitions
from backend.app.drivers.yoosee.p2p.video_startup_645 import with_preconnect_definition


@pytest.mark.parametrize("definition,legacy,packed", [
    (1, 0, b"\x49\x12"), (2, 1, b"\x92\x24"),
    (3, 2, b"\xdb\x36"), (7, 6, b"\xff\x7f"),
])
def test_sdk_unknown_platform_branch_changes_only_both_cached_fields(definition, legacy, packed):
    original = bytes(range(32))
    result = with_preconnect_definition(original, definition)
    assert result == bytes((legacy,)) + original[1:23] + packed + original[25:]
    assert original == bytes(range(32))
    assert with_preconnect_definition(result, definition) == result
    assert build_av_init(42, request_user_data=result, connection_type=1)[24:56] == result


@pytest.mark.parametrize("value", [True, 3.0, "3", None, 0, 4, -1, 8, {0: 3}])
def test_only_known_uniform_enums(value):
    with pytest.raises(ValueError):
        with_preconnect_definition(bytes(32), value)


@pytest.mark.parametrize("template", [None, bytearray(32), "a" * 32, bytes(31), bytes(33)])
def test_requires_reviewed_immutable_template(template):
    with pytest.raises(ValueError):
        with_preconnect_definition(template, 3)


def test_preparation_never_relaxes_midstream_platform_gate_or_default():
    original = build_av_init(42)
    prepared = with_preconnect_definition(original[24:56], 3)
    assert prepared[0] == 2 and prepared[23:25] == b"\xdb\x36"
    assert build_av_init(42) == original
    with pytest.raises(ValueError):
        encode_definitions(0, {0: 3})
