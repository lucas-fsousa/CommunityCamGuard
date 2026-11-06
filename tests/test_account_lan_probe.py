"""LAN measurements share the broker reader; no camera or extra socket access."""

import logging
import struct

import pytest

from backend.app.drivers.yoosee.p2p.account_lan_probe import AccountLanProbe
from backend.app.drivers.yoosee.p2p.account_output_budget import AccountOutputBudget
from backend.app.drivers.yoosee.p2p.broker_sequences import BrokerSequences
from backend.app.drivers.yoosee.p2p.crypto import gute_mode1_xor_checksum, gute_mode2_decrypt
from backend.app.drivers.yoosee.p2p.media_protocol import build_media_meter_ack, build_mtp_frame
from backend.app.drivers.yoosee.p2p.media_route_job import MediaRouteJob
from backend.app.drivers.yoosee.p2p.wire import finish_mode2
from tests.test_account_media_route import SPEC
from tests.test_broker_reads import NODE
from tests.test_media_lease_inbox import bootstrap, camera
from tests.test_media_lease_worker import execute, setup
from tests.test_tcp_route import advertisement

PEER = ("192.168.1.50", 58654)


def lan_advertisement(frame):
    frame = bytearray(frame)
    frame[0x60:0x64] = bytes((198, 51, 100, 9))
    frame[0x64:0x68] = bytes((192, 168, 1, 50))
    struct.pack_into("<H", frame, 0x5A, PEER[1])
    struct.pack_into("<I", frame, 16, gute_mode1_xor_checksum(frame))
    return bytes(frame)


def route(now):
    job = MediaRouteJob(NODE, BrokerSequences(NODE.session_id, 1, 40),
        AccountOutputBudget(), SPEC, generation=1, local_ip="192.0.2.1", local_port=1234,
        clock=lambda: now[0])
    job.next_packet()
    frame = bytearray(advertisement(link=job.attempt.link_id))
    struct.pack_into("<Q", frame, 4, NODE.session_id)
    job.observe(lan_advertisement(frame), NODE.address, generation=1)
    return job


@pytest.mark.parametrize("fault", [None, "peer", "link", "timestamp", "request", "late"])
def test_meter_requires_pinned_peer_and_exact_fresh_roundtrip(fault, caplog):
    caplog.set_level(logging.INFO)
    now = [0.0]
    job = route(now)
    probe = AccountLanProbe(clock=lambda: now[0])
    (wire, peer), = probe.poll((job,))
    assert peer == PEER and wire[:2] == b"\xc0\x90"
    assert probe.poll((job,)) == ()
    inner = bytearray(build_media_meter_ack(wire)[6:])
    if fault == "link":
        struct.pack_into("<I", inner, 4, job.attempt.link_id + 1)
    if fault == "timestamp":
        struct.pack_into("<I", inner, 32, 99)
    if fault == "request":
        inner[1] = 1
    if fault == "late":
        now[0] = 1.5
    probe.receive(build_mtp_frame(0x90, inner), ("192.168.1.51", PEER[1]) if fault == "peer" else PEER)
    assert job.lan_roundtrip_confirmed is (fault is None)
    now[0] = 1.5 if fault == "late" else 0.3
    second = probe.poll((job,))
    assert len(second) == (0 if fault in (None, "late") else 1)
    now[0] = 2.0
    assert probe.poll((job,)) == ()
    assert probe.poll((job,)) == ()
    job.cancel()
    assert probe.poll((job,)) == ()
    assert not probe._probes
    assert "192.168" not in caplog.text


@pytest.mark.parametrize("reply", [True, False])
def test_existing_udp_nat_exchange_is_cooperative_and_correlated(reply):
    from backend.app.drivers.yoosee.p2p.rendezvous_protocol import build_nat_online

    now = [0.0]
    job = route(now)
    job.nat_endpoint = ("8.8.4.4", 32100)
    probe = AccountLanProbe(clock=lambda: now[0])
    packets = probe.poll((job,))
    assert len(packets) == 4
    assert sum(wire[:2] == b"\x7f\xca" for wire, _ in packets) == 3
    meter = next(wire for wire, _ in packets if wire[:2] == b"\xc0\x90")
    probe.receive(build_media_meter_ack(meter), PEER)
    assert job.lan_roundtrip_confirmed and not job.nat_handshake_confirmed
    incoming = build_nat_online(job.device_id, job.access_id, job.attempt.link_id)
    probe.receive(incoming, ("8.8.4.5", 32100))
    assert not job.nat_handshake_confirmed
    if reply:
        probe.receive(incoming, job.nat_endpoint)
        responses = probe.poll((job,))
        assert [wire[:2] for wire, _ in responses] == [b"\x7f\xca", b"\x7f\xcb"]
    now[0] = 1.5
    probe.poll((job,))
    assert job.nat_handshake_complete and job.nat_handshake_confirmed is reply


@pytest.mark.parametrize("fault", ["send", "silent"])
def test_existing_worker_handles_lan_reply_while_tcp_and_broker_keep_running(monkeypatch, caplog, fault):
    caplog.set_level(logging.INFO)
    sock, inbox, pumps, owner = setup(monkeypatch)
    ticket = inbox.submit(camera(0), bootstrap(0))
    send, receive = sock.sendto, sock.recvfrom
    direct, pending = [], []

    def sendto(wire, peer):
        if peer == PEER:
            direct.append(wire)
            if fault == "send":
                raise OSError("synthetic unavailable LAN route")
            if fault != "silent":
                pending.append((build_media_meter_ack(wire), PEER))
            return len(wire)
        result = send(wire, peer)
        if wire[1] == 0xA4:
            plain = gute_mode2_decrypt(sock.queue[-1], NODE.session_key)
            sock.queue[-1] = finish_mode2(bytearray(lan_advertisement(plain)), NODE.session_key)
        return result

    def recvfrom(size):
        if pending:
            return pending.pop(0)
        return receive(size)

    monkeypatch.setattr(sock, "sendto", sendto)
    monkeypatch.setattr(sock, "recvfrom", recvfrom)
    result = execute(sock, inbox, owner, duration=2)
    assert result.reason == "stopped" and not result.lease_cleanup_uncertain
    assert len(direct) == (1 if fault is None else 2)
    assert all(wire[:2] == b"\xc0\x90" for wire in direct)
    assert ("outcome=roundtrip" if fault is None else "outcome=no_roundtrip") in caplog.text
    assert sock.opens == sock.closes == sock.closed == 1
    assert pumps[0].steps > 3 and pumps[0].closed == 1
    assert inbox.take(ticket).status == "closed"


@pytest.mark.parametrize("silent_av", [False, True])
def test_udp_video_and_failed_negotiation_fallback_use_the_same_account_reader(monkeypatch, caplog, silent_av):
    from backend.app.drivers.yoosee.p2p.media_protocol import parse_kcp_segments, parse_media_meter
    from backend.app.drivers.yoosee.p2p.stream_protocol import encrypt_media_tlv
    from tests.test_av_handshake import ack
    from tests.test_captured_media import control, header
    from tests.test_hevc_recovery import idr
    from tests.test_media_receive import packet
    from tests.test_v1_receive import av

    caplog.set_level(logging.INFO)
    sock, inbox, pumps, owner = setup(monkeypatch)
    item = bootstrap(0)
    records = []
    consume = item.video.consume
    def capture(record):
        records.append(record)
        consume(record)
    monkeypatch.setattr(item.video, "consume", capture)
    ticket = inbox.submit(camera(0), item)
    send, receive = sock.sendto, sock.recvfrom
    pending, attempts, replacements, outcomes = [], [], [], []

    def sendto(wire, peer):
        if peer != PEER:
            result = send(wire, peer)
            if wire[1] == 0xA4:
                request = gute_mode2_decrypt(wire, NODE.session_key)
                attempts.append((struct.unpack_from("<I", request, 28)[0], request[0x78:0x80]))
                plain = gute_mode2_decrypt(sock.queue[-1], NODE.session_key)
                sock.queue[-1] = finish_mode2(bytearray(lan_advertisement(plain)), NODE.session_key)
            return result
        if wire[:2] == b"\x7e\xa4":
            return len(wire)  # SDK sends it, but its receipt is not required for AV readiness.
        meter = parse_media_meter(wire)
        if meter is not None:
            if meter.kind == 1:
                pending.append((build_media_meter_ack(wire), peer))
            return len(wire)
        segment, = parse_kcp_segments(wire)
        if not segment.body or silent_av:
            return len(wire)
        pending.append((ack(wire), peer))
        action = struct.unpack_from("<I", segment.body, 8)[0]
        call = struct.unpack_from("<I", segment.body, 4)[0]
        link, cookie = attempts[-1]
        if action == 1:
            pending.append((packet(0, 0, control(call), conv=link | 0x80000000), peer))
            start = bytearray(control(call))
            struct.pack_into("<I", start, 8, 6)
            pending.append((packet(0, 0, start, conv=link), peer))
        elif action == 6:
            body = encrypt_media_tlv(header() + av(video=idr(), video_ts=1), cookie)
            pending.append((packet(1, 0, body, conv=link), peer))
        return len(wire)

    def recvfrom(size):
        if silent_av and not replacements:
            outcome = inbox.take(ticket)
            if outcome is not None:
                outcomes.append(outcome)
                replacements.append(inbox.submit(camera(0), bootstrap(0)))
        if pending:
            return pending.pop(0)
        return receive(size)

    monkeypatch.setattr(sock, "sendto", sendto)
    monkeypatch.setattr(sock, "recvfrom", recvfrom)
    result = execute(sock, inbox, owner, duration=3 if silent_av else 0.2)
    assert result.reason == "stopped" and not result.lease_cleanup_uncertain
    if silent_av:
        assert outcomes[0].channel.protocol_code == "av_control_timeout"
        assert outcomes[0].channel.udp.detached and outcomes[0].channel.media.broker_release
        assert len(pumps) == len(replacements) == 1
        assert inbox.take(replacements[0]).channel.tcp.ready
        assert sock.opens == sock.closes == 2
    else:
        outcome = inbox.take(ticket)
        assert outcome.channel.udp.ready and outcome.channel.udp.detached
        assert outcome.channel.media.broker_release and outcome.channel.tcp is None
        assert any(record.video == idr() for record in records)
        assert not pumps and sock.opens == sock.closes == 1
        assert "native UDP AV ready transport=lan" in caplog.text
    assert sock.closed == 1
