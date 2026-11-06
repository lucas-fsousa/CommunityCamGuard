"""Existing AV wire engine on a measured LAN route, never a real camera."""

import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_media_channel import AccountMediaChannel, MediaBootstrap
from backend.app.drivers.yoosee.p2p.contracts import CallingAttempt
from backend.app.drivers.yoosee.p2p.cooperative_udp_media import CooperativeUdpMedia
from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.media_policy import MediaPolicy
from backend.app.drivers.yoosee.p2p.media_protocol import (
    build_media_meter_request,
    build_mtp_frame,
    parse_kcp_segments,
)
from backend.app.drivers.yoosee.p2p.video_delivery import VideoDelivery
from tests.test_account_lan_probe import PEER, route
from tests.test_account_media_route import SPEC
from tests.test_av_handshake import ack
from tests.test_av_receive import COOKIE, media
from tests.test_av_receive_channels import CONTROL, start
from tests.test_captured_media import control, header
from tests.test_hevc_recovery import idr
from tests.test_media_receive import packet
from tests.test_v1_receive import av


def session():
    now, sent, video = [0.0], [], VideoDelivery()
    job = route(now)
    job.attempt = CallingAttempt(42, 123, COOKIE)
    job.lan_probe_complete = job.lan_roundtrip_confirmed = True
    owner = CooperativeUdpMedia(job, SPEC, lambda wire, peer: sent.append((wire, peer)),
                                clock=lambda: now[0], video=video)
    return owner, job, now, sent, video


def negotiate(owner, sent, flagged=False):
    def incoming(wire):
        return build_mtp_frame(0x18, wire[6:]) if flagged else wire
    owner.step()
    init = sent[-1][0]
    assert owner.receive(incoming(ack(init)), PEER)
    owner.receive(incoming(packet(0, 0, control(123), conv=CONTROL)), PEER)
    owner.receive(incoming(packet(0, 0, start())), PEER)
    owner.step()
    local_start = sent[-1][0]
    owner.receive(incoming(ack(local_start)), PEER)
    owner.receive(incoming(packet(1, 0, media(header() + av(video=idr(), video_ts=1)))), PEER)
    assert owner.ready and owner.was_ready


@pytest.mark.parametrize("flagged", [False, True])
def test_lan_negotiation_delivers_existing_hevc_handoff_and_closes_without_owning_socket(flagged):
    owner, _, _, sent, video = session()
    negotiate(owner, sent, flagged=flagged)
    assert video.take().payload == idr()
    owner.begin_finish()
    owner.step()
    close = sent[-1][0]
    assert struct.unpack_from("<I", parse_kcp_segments(close)[0].body, 8)[0] == 7
    owner.receive(ack(close), PEER)
    result = owner.finish()
    assert result.ready and result.detached and result.close_acknowledged
    assert result.video_frames == result.encoding_headers == 1
    assert video.closed and owner.finish() is result


def test_bad_peer_conv_and_checksum_cannot_bypass_foreign_input_budget():
    owner, _, _, sent, _ = session()
    owner.step()
    assert not owner.receive(ack(sent[-1][0]), (PEER[0], PEER[1] + 1))
    assert not owner.receive(packet(0, 0, b"invalid", conv=44), PEER)
    assert not owner.receive(bytes(78), PEER)
    damaged = bytearray(build_mtp_frame(0x18, ack(sent[-1][0])[6:]))
    damaged[4] ^= 1
    assert not owner.receive(bytes(damaged), PEER)
    assert not owner.receive(build_mtp_frame(0x38, ack(sent[-1][0])[6:]), PEER)
    assert not owner.ready
    assert owner.finish().detached


def test_lost_udp_negotiation_clears_parser_and_delivery_for_tcp_fallback():
    owner, _, now, sent, video = session()
    owner.step()
    now[0] = 2
    with pytest.raises(ReceiveError):
        owner.step()
    assert owner.result.detached and not owner.result.ready and video.closed
    assert [wire[:2] for wire, _ in sent] == [b"\x7e\xa4", b"\xc0\x10"]


def test_measured_lan_selection_never_constructs_tcp_for_the_same_call():
    now, sent, video = [0.0], [], VideoDelivery()
    job = route(now)
    job.lan_probe_complete = job.lan_roundtrip_confirmed = True
    channel = AccountMediaChannel(MediaBootstrap(SPEC, video), job,
                                   policy=MediaPolicy.CONTINUOUS, clock=lambda: now[0])
    def no_tcp(*args, **kwargs):
        pytest.fail("LAN-selected call must not also open TCP")
    channel.step(no_tcp, udp_send=lambda wire, peer: sent.append((wire, peer)))
    assert channel.udp is not None and channel.tcp is None
    channel.retire("ended")
    assert channel.summary().udp.detached and channel.summary().tcp is None


def test_continuous_meter_responder_has_no_finite_64_reply_cutoff():
    owner, _, _, sent, _ = session()
    for sequence in range(70):
        request = build_media_meter_request(SPEC.device.device_id, SPEC.access_id, 42, 123,
                                            sequence=sequence, timestamp=sequence)
        assert owner.receive(request, PEER)
    assert len(sent) == 70
    assert owner.finish().detached


def test_cancelling_expired_udp_parser_detaches_without_failing_sibling_account():
    owner, _, now, sent, _ = session()
    negotiate(owner, sent)
    now[0] = 10
    owner.begin_finish()
    assert owner.result.detached and not owner.result.close_acknowledged
