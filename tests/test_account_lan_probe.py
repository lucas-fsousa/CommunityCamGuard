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


@pytest.mark.parametrize("fault", [None, "send", "silent"])
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
