"""Offline wire coherence; these tests never create a socket or claim HD support."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.contracts import CallingAttempt, CertifiedNode, OnlineDevice
from backend.app.drivers.yoosee.p2p.crypto import gute_mode1_decrypt, gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.media_protocol import build_av_init
from backend.app.drivers.yoosee.p2p.rendezvous_protocol import (
    build_calling_request,
    build_direct_calling_request,
)
from backend.app.drivers.yoosee.p2p.video_definition import with_startup_definitions


def route():
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    device = OnlineDevice(7_000_000_002, 1, False, 1, bytes(16))
    attempt = CallingAttempt(0x123456, 0x89ABCDEF, bytes(8))
    return node, (node, 123, device, "192.0.2.20", 45678, attempt, 18)


@pytest.mark.parametrize("platform", [1, 2])
@pytest.mark.parametrize("definition", [1, 2, 3, 7])
def test_live_quality_userdata_is_identical_in_broker_direct_and_init(platform, definition):
    node, args = route()
    original = build_av_init(1)[24:56]
    metadata = with_startup_definitions(original, platform, dict.fromkeys(range(5), definition))
    kwargs = dict(request_user_data=metadata, connection_type=1)
    broker = gute_mode2_decrypt(build_calling_request(*args, **kwargs), node.session_key)
    direct = gute_mode1_decrypt(build_direct_calling_request(*args, **kwargs))
    init = build_av_init(0x89ABCDEF, **kwargs)
    assert broker[0x90:0xB0] == direct[0x90:0xB0] == init[24:56] == metadata
    assert broker[0xB0] == direct[0xB0] == metadata[0]
    assert not direct[0xB0] & 0xC0  # No playback or other-mode flag added to live.
    assert struct.unpack_from("<I", init, 16)[0] == 1
    assert len(init) == 76 and len(direct) == len(broker) == 177


def test_explicit_captured_live_metadata_preserves_direct_and_init_defaults():
    _, args = route()
    init = build_av_init(0x89ABCDEF)
    explicit = dict(request_user_data=init[24:56], connection_type=1)
    assert build_av_init(0x89ABCDEF, **explicit) == init
    default_direct = gute_mode1_decrypt(build_direct_calling_request(*args))
    explicit_direct = gute_mode1_decrypt(build_direct_calling_request(*args, **explicit))
    # Randomized transport header/checksum precede application fields.
    assert explicit_direct[0x18:] == default_direct[0x18:]


@pytest.mark.parametrize("connection_type", [True, False, 1.0, "1", 0, 3, None])
def test_explicit_metadata_rejects_unknown_or_coercible_connection_type(connection_type):
    _, args = route()
    kwargs = dict(request_user_data=bytes(32), connection_type=connection_type)
    for builder in (build_calling_request, build_direct_calling_request):
        with pytest.raises(ValueError):
            builder(*args, **kwargs)
    with pytest.raises(ValueError):
        build_av_init(1, **kwargs)
