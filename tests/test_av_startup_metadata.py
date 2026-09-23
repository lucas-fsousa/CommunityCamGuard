"""Bounded diagnostic metadata propagation without camera/network access."""

import struct
from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p import av_route
from backend.app.drivers.yoosee.p2p.av_control_send import ReliableAvControl
from backend.app.drivers.yoosee.p2p.av_sample import AvVideoSample
from backend.app.drivers.yoosee.p2p.media_protocol import KCP_PUSH, parse_kcp_segments
from backend.app.drivers.yoosee.p2p.video_definition import with_startup_definitions
from tests.test_av_probe import CALLING, CHANNEL, FakeSocket, run
from tests.test_av_route import ENROLLMENT, RESULT, env  # noqa: F401
from tests.test_media_receive import PEER

METADATA = with_startup_definitions(bytes(32), 2, dict.fromkeys(range(5), 3))


def test_init_retries_keep_exact_reviewed_metadata():
    now = [0.0]
    sender = ReliableAvControl(PEER, 42, 123, action=1, request_user_data=METADATA,
                               clock=lambda: now[0])
    wire = sender.due()
    segment, = parse_kcp_segments(wire)
    assert segment.body[24:56] == METADATA
    assert struct.unpack_from("<I", segment.body, 16)[0] == 1
    for instant in (0.25, 0.5, 0.75):
        now[0] = instant
        assert sender.due() == wire
    sender.close()
    assert sender.closed


def test_real_receive_coordinator_passes_metadata_only_to_init():
    sock = FakeSocket()
    result = run(sock, duration=0.5, request_user_data=METADATA)
    assert result.ready and result.close_acknowledged and sock.closed
    controls = [s for wire in sock.sent for s in parse_kcp_segments(wire) if s.command == KCP_PUSH]
    assert [struct.unpack_from("<I", s.body, 8)[0] for s in controls] == [1, 6, 7]
    assert controls[0].body[24:56] == METADATA
    assert all(s.body[24:56] == bytes(32) for s in controls[1:])


@pytest.mark.parametrize("action,sequence", [(6, 0), (7, 1)])
def test_metadata_cannot_be_attached_to_start_or_close(action, sequence):
    with pytest.raises(ValueError, match="only valid for INIT"):
        ReliableAvControl(PEER, 42, 123, action=action, sequence=sequence,
                          request_user_data=METADATA)


@pytest.mark.parametrize("metadata", [bytes(31), bytes(33), bytearray(32), "x" * 32])
def test_invalid_metadata_rejected_before_route_and_clears_sample(env, metadata):  # noqa: F811
    sample = AvVideoSample()
    with pytest.raises(ValueError):
        av_route.probe_av_route(ENROLLMENT, camera_id="cam_test", device_id="123",
                                request_user_data=metadata, sample=sample)
    assert env == [] and sample.closed


def test_route_passes_same_immutable_value_to_all_stages_and_cleans_up(env, monkeypatch):  # noqa: F811
    seen = []
    def calling(*args, **kwargs):
        seen.append(kwargs["request_user_data"])
        assert kwargs["connection_type"] == 1 and kwargs["retries"] == 1
        return replace(CALLING, attempt=kwargs["attempt"])
    def media(*args, **kwargs):
        seen.append(kwargs["request_user_data"])
        assert kwargs["connection_type"] == 1 and kwargs["require_roundtrip"]
        return CHANNEL
    def probe(*args, **kwargs):
        seen.append(kwargs["request_user_data"])
        assert not kwargs["close_socket"]
        return RESULT
    monkeypatch.setattr(av_route, "call_device", calling)
    monkeypatch.setattr(av_route, "open_media_channel", media)
    monkeypatch.setattr(av_route, "probe_av_socket", probe)
    result = av_route.probe_av_route(ENROLLMENT, camera_id="cam_test", device_id="123",
                                     request_user_data=METADATA)
    assert result.route_release_acknowledged
    assert len(seen) == 3 and all(value is METADATA for value in seen)
    assert env[-2][0] == "release" and env[-1] == "socket_close"
