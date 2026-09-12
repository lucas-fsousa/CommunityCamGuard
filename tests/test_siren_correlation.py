"""Synthetic encrypted siren replies only; no sockets or camera actions."""
import struct

import pytest

from backend.app.drivers.yoosee.capability_evidence import EvidenceState
from backend.app.drivers.yoosee.p2p import siren
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice
from backend.app.drivers.yoosee.p2p.wire import finish_mode2, new_header
from backend.app.drivers.yoosee.siren_evidence import siren_evidence, siren_state

NODE = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
DEVICE = OnlineDevice(7000000002, 1, False, 1, bytes(16))


def reply(*, session=9, mode=2, message=44, ack=False, sequence=18):
    frame = new_header(0xAC if ack else 0xAD, 0x36, session, sequence,
                       (mode << 16) | (int(ack) << 20))
    frame[0] = 0x7E
    struct.pack_into("<I", frame, 0x30, message)
    struct.pack_into("<H", frame, 0x34, 0)
    return finish_mode2(frame, NODE.session_key) if mode == 2 else bytes(frame)


def exchange(monkeypatch, frames):
    sent = []

    class Socket:
        def sendto(self, frame, peer):
            sent.append(frame)

    monkeypatch.setattr(siren.secrets, "randbits", lambda _: 44)
    monkeypatch.setattr(siren, "receive_datagrams", lambda *_: iter(frames))
    monkeypatch.setattr(siren, "acknowledge_reliable_node_frame", lambda *_: None)
    result = siren.exchange_siren_action(Socket(), NODE, 123, DEVICE, True, 18, 1,
                                        retries=1, require_correlated_response=True)
    assert len(sent) == 1
    return result


@pytest.mark.parametrize("changes", [{"session": 10}, {"mode": 0}, {"message": 45}])
def test_unrelated_response_never_confirms_action(monkeypatch, changes):
    assert exchange(monkeypatch, [(reply(**changes), NODE.address)]).error_code is None


def test_only_current_session_message_is_accepted(monkeypatch):
    result = exchange(monkeypatch, [(reply(message=45), NODE.address), (reply(), NODE.address)])
    assert result.error_code == 0
    assert not result.transport_acknowledged


@pytest.mark.parametrize("sequence,expected", [(18, True), (17, False)])
def test_transport_ack_is_correlated_and_not_application_confirmation(monkeypatch, sequence, expected):
    result = exchange(monkeypatch, [(reply(ack=True, sequence=sequence), NODE.address)])
    assert result.transport_acknowledged is expected
    assert result.error_code is None


def test_other_peer_is_ignored(monkeypatch):
    assert exchange(monkeypatch, [(reply(), ("192.0.2.11", 19800))]).error_code is None


@pytest.mark.parametrize("value", [
    None, 1, True, {"stVal": 1}, {"t": 0, "stVal": 1}, {"t": True, "stVal": 1},
    {"t": 1.0, "stVal": 1}, {"t": 1, "stVal": True}, {"t": 1, "stVal": 0},
    {"t": 1, "nested": {"stVal": 1}}, {"t": 1, "stVal": 0, "other": 1},
    {"t": 0x80000000, "stVal": 1},
])
def test_exact_state_never_borrows_timestamp_or_other_integer(value):
    assert siren_state(value) is None
    assert siren_evidence(value) == EvidenceState.UNKNOWN


@pytest.mark.parametrize("state", [1, 2])
def test_supported_state_is_not_a_pulse_proof(state):
    assert siren_state({"t": 12, "stVal": state}) == state
    assert siren_evidence({"t": 12, "stVal": state}) == EvidenceState.SUPPORTED
    assert siren_evidence({"t": -1, "stVal": state}) == EvidenceState.UNSUPPORTED
