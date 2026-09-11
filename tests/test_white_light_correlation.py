"""Socket-free encrypted floodlight replies: transport receipts are not state proof."""
import json
import struct

import pytest

from backend.app.drivers.yoosee.capability_evidence import EvidenceState
from backend.app.drivers.yoosee.p2p import white_light as light
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice
from backend.app.drivers.yoosee.p2p.wire import finish_mode2, new_header
from tests.test_yoosee_capability_snapshot import DEVICE as DEVICE_ID
from tests.test_yoosee_capability_snapshot import batch, normalize

NODE = CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
DEVICE = OnlineDevice(7000000002, 1, False, 1, bytes(16))


def reply(*, request_id=44, session=9, destination=123, source=7000000002,
          mode=2, kind=12, enabled=0, ack=False, sequence=18):
    data = json.dumps({"type": kind, "data": {"whiteLightStatus": enabled}}).encode()
    payload = b"\x01\x00\x00\x00" + struct.pack("<I", request_id) + data
    frame = new_header(0xB9, 0x34 + len(payload), session, sequence,
                       (mode << 16) | (1 << 18) | (int(ack) << 20))
    frame[0] = 0x7E
    struct.pack_into("<Q", frame, 0x1C, destination)
    struct.pack_into("<Q", frame, 0x24, source)
    struct.pack_into("<I", frame, 0x2C, 44)
    struct.pack_into("<H", frame, 0x30, len(payload))
    frame[0x34:] = payload
    return finish_mode2(frame, NODE.session_key) if mode == 2 else bytes(frame)


def exchange(monkeypatch, frames):
    sent = []
    class Socket:
        def sendto(self, frame, peer):
            sent.append((frame, peer))
    monkeypatch.setattr(light.secrets, "randbits", lambda _bits: 44)
    monkeypatch.setattr(light, "receive_datagrams", lambda *_args: iter(frames))
    monkeypatch.setattr(light, "acknowledge_reliable_node_frame", lambda *_args: None)
    result = light.exchange_white_light(
        Socket(), NODE, 123, DEVICE, None, 18, 1, retries=1,
        require_correlated_response=True,
    )
    return result, sent


@pytest.mark.parametrize("changes", [
    {"request_id": 45}, {"session": 10}, {"destination": 124},
    {"source": 7000000003}, {"mode": 0}, {"kind": 11}, {"kind": 12.0},
])
def test_unrelated_or_unencrypted_response_never_proves_state(monkeypatch, changes):
    result, sent = exchange(monkeypatch, [(reply(**changes), NODE.address)])
    assert result.response is None
    assert len(sent) == 1  # no application receipt for a rejected state


def test_wrong_peer_is_ignored(monkeypatch):
    result, _ = exchange(monkeypatch, [(reply(), ("192.0.2.11", 19800))])
    assert result.response is None


@pytest.mark.parametrize("error", [20001, -1, False, "0"])
def test_application_error_cannot_be_normalized_into_a_light_state(error):
    assert light.extract_white_light_state({
        "type": 12, "err": error, "data": {"whiteLightStatus": 0},
    }) is None


@pytest.mark.parametrize("enabled", [0, 1])
def test_accepts_only_matching_reply_after_stale_state(monkeypatch, enabled):
    result, sent = exchange(monkeypatch, [
        (reply(request_id=45, enabled=1-enabled), NODE.address),
        (reply(enabled=enabled), NODE.address),
    ])
    assert light.extract_white_light_state(result.response) is bool(enabled)
    assert len(sent) == 2  # request plus BA receipt for the actual response


@pytest.mark.parametrize("sequence,expected", [(18, True), (17, False)])
def test_ack_correlates_sequence_but_never_counts_as_application_state(monkeypatch, sequence, expected):
    result, _ = exchange(monkeypatch, [(reply(ack=True, sequence=sequence), NODE.address)])
    assert result.transport_acknowledged is expected
    assert result.response is None


@pytest.mark.parametrize("enabled", [False, True])
def test_snapshot_uses_binary_application_state_without_inventing_timestamp(enabled):
    state = light.P2PWhiteLightState(DEVICE_ID, enabled, True, False, False, False)
    snapshot = normalize((*batch(), state))
    evidence = next(item for item in snapshot.evidence if item.feature == "white_light")
    assert evidence.state == EvidenceState.SUPPORTED
    assert evidence.property_timestamp is None
    assert normalize((*batch(), state, state)) is None


def test_missing_malformed_or_unrelated_light_state_cannot_grant_support():
    from dataclasses import replace
    state = light.P2PWhiteLightState(DEVICE_ID, False, True, False, True, True)
    for observations in (batch(), (*batch(), replace(state, enabled=0))):
        evidence = next(i for i in normalize(observations).evidence if i.feature == "white_light")
        assert evidence.state == EvidenceState.UNKNOWN
    for changed in (replace(state, device_id="7000000003"), replace(state, authenticated=False)):
        assert normalize((*batch(), changed)) is None


@pytest.mark.parametrize("migrated", [False, True])
def test_strict_dashboard_read_is_scoped_to_migrated_units(monkeypatch, migrated):
    from types import SimpleNamespace

    from backend.app.drivers.yoosee import capability_rollout, controls

    calls = []
    state = light.P2PWhiteLightState(DEVICE_ID, False, True, False, True, True)
    def read(_access, **kwargs):
        calls.append(kwargs)
        return state
    monkeypatch.setattr(controls, "_enrollment", lambda _camera: object())
    monkeypatch.setattr(controls, "run_with_fresh_access", lambda access, fn: fn(access))
    monkeypatch.setattr(controls, "read_camera_white_light", read)
    monkeypatch.setattr(capability_rollout, "selected", lambda _id: (
        (None, frozenset({"white_light"})) if migrated else None
    ))
    result = controls.read(SimpleNamespace(camera_id="cam_" + "1" * 24), "white_light")
    assert result.value is False and result.verified
    assert calls == ([{"require_correlated_response": True}] if migrated else [{}])
