from __future__ import annotations

import json
import struct

import pytest

from backend.app.drivers.yoosee.p2p import model_session
from backend.app.drivers.yoosee.p2p.contracts import CertifiedNode, OnlineDevice
from backend.app.drivers.yoosee.p2p.crypto import gute_mode2_encrypt
from backend.app.drivers.yoosee.p2p.wire import new_header


def test_model_read_correlates_transport_ack_and_nested_report(monkeypatch):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    device = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    path = "ProWritable.videoParm"
    ack = bytearray(32)
    ack[1] = 0xB7
    struct.pack_into("<I", ack, 0x0C, 18)
    struct.pack_into("<I", ack, 0x14, 1 << 20)
    report = bytearray(32)
    report[1] = 0xAA
    sent: list[tuple[bytes, tuple[str, int]]] = []
    acknowledged: list[bytes] = []

    class FakeSocket:
        def sendto(self, payload, address):
            sent.append((payload, address))

    monkeypatch.setattr(model_session.secrets, "randbits", lambda _bits: 19)
    monkeypatch.setattr(model_session, "build_model_read", lambda *_args: b"request")
    monkeypatch.setattr(
        model_session,
        "receive_datagrams",
        lambda *_args: iter(((b"ack", node.address), (b"report", node.address))),
    )
    monkeypatch.setattr(
        model_session,
        "decrypt_node_frame",
        lambda wire, _node: bytes(ack if wire == b"ack" else report),
    )
    monkeypatch.setattr(
        model_session,
        "parse_model_report",
        lambda frame: (
            (device.device_id, path + ".setVal", {"multiFlip": 1}) if frame[1] == 0xAA else None
        ),
    )
    monkeypatch.setattr(
        model_session,
        "acknowledge_reliable_node_frame",
        lambda _sock, _node, frame: acknowledged.append(frame),
    )

    result = model_session.exchange_model_read(
        FakeSocket(),  # type: ignore[arg-type]
        node,
        device,
        path,
        18,
        0.1,
        retries=1,
    )

    assert result.transport_acknowledged is True
    assert result.error_code == 0
    assert result.value == {"multiFlip": 1}
    assert sent == [(b"request", node.address)]
    assert acknowledged == [bytes(report)]


@pytest.mark.parametrize("valid_last", [False, True])
def test_correlated_reads_reject_unsolicited_observations(monkeypatch, valid_last):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    device = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    path = "ProWritable.videoParm"
    observation = {"t": 1787545850, "setVal": {"multiFlip": 1}}
    frames = []
    reports = {}

    def add(subtype, seq=0, report=None, ack=False):
        frame = bytearray(40)
        frame[1] = subtype
        struct.pack_into("<I", frame, 0x0C, seq)
        struct.pack_into("<I", frame, 0x14, (1 << 20) if ack else 0)
        frame[0x18] = len(frames)
        raw = bytes(frame)
        frames.append((raw, node.address))
        if report is not None:
            reports[raw] = report

    add(0xB7, 17, ack=True)  # Old transport ACK is not this read's ACK.
    add(0xB8)  # Even a parseable direct reply lacks proven wire correlation.
    for destination, report_path in (
        (None, path),
        (device.device_id + 1, path),
        (device.device_id, path + ".setVal"),
        (device.device_id, "ProWritable"),
        (device.device_id, "ProWritable.guardParm"),
        (device.device_id, path),
    ):
        add(0xAA, report=(destination, report_path, observation))
    if valid_last:
        add(0xB7, 18, ack=True)
        frames.append((_response(node, device, observation), node.address))

    class FakeSocket:
        def sendto(self, *_args):
            pass

    monkeypatch.setattr(model_session, "build_model_read", lambda *_args: b"request")
    monkeypatch.setattr(model_session, "receive_datagrams", lambda *_args: iter(frames))
    monkeypatch.setattr(model_session, "decrypt_node_frame", lambda wire, _node: wire)
    monkeypatch.setattr(model_session, "parse_model_report", reports.get)
    monkeypatch.setattr(model_session, "acknowledge_reliable_node_frame", lambda *_args: True)
    result = model_session.exchange_model_read(
        FakeSocket(),
        node,
        device,
        path,
        18,
        0.1,  # type: ignore[arg-type]
        retries=1,
        require_correlated_response=True,
    )
    assert result.transport_acknowledged is valid_last
    assert result.error_code == (0 if valid_last else None)
    assert result.value == (observation if valid_last else None)


def _response(node, device, value, *, sequence=18, flags=(2 << 16) | (1 << 21)):
    encoded = json.dumps(value).encode()
    frame = new_header(0xB8, 0x28 + len(encoded), node.session_id, 900, flags)
    struct.pack_into("<I", frame, 0x10, sequence)
    struct.pack_into("<Q", frame, 0x18, device.device_id)
    frame[0x20] = 1
    struct.pack_into("<H", frame, 0x26, len(encoded))
    frame[0x28:] = encoded
    return bytes(frame)


@pytest.mark.parametrize(
    "mutation", [None, "old", "checksum", "device", "session", "ack", "truncated_json", "error"]
)
def test_b8_correlation_after_real_mode2_decryption(monkeypatch, mutation):
    node = CertifiedNode(("192.0.2.10", 19800), 9, bytes(32), 17)
    device = OnlineDevice(7000000002, 1, False, 1, bytes(16))
    value = {"t": 1787545850, "setVal": {"multiFlip": 1}}
    frame = bytearray(_response(node, device, value))
    if mutation == "old":
        struct.pack_into("<I", frame, 0x10, 17)
    elif mutation == "checksum":
        struct.pack_into("<I", frame, 0x14, 2 << 16)
    elif mutation == "device":
        struct.pack_into("<Q", frame, 0x18, device.device_id + 1)
    elif mutation == "session":
        struct.pack_into("<Q", frame, 4, node.session_id + 1)
    elif mutation == "ack":
        struct.pack_into("<I", frame, 0x14, (2 << 16) | (1 << 21) | (1 << 20))
    elif mutation == "truncated_json":
        struct.pack_into("<H", frame, 0x26, len(frame))
    elif mutation == "error":
        frame[0x20] = 0
        struct.pack_into("<H", frame, 0x24, 20001)
    # Native response construction preserves request sequence at 0x10 instead
    # of replacing it with a checksum: do not use finish_mode2 here.
    wire = gute_mode2_encrypt(bytes(frame), node.session_key)

    class FakeSocket:
        def sendto(self, *_args):
            pass

    monkeypatch.setattr(
        model_session, "receive_datagrams", lambda *_args: iter(((wire, node.address),))
    )
    result = model_session.exchange_model_read(
        FakeSocket(),
        node,
        device,
        "ProWritable.videoParm",
        18,
        0.1,
        retries=1,
        require_correlated_response=True,
    )
    assert result.error_code == (0 if mutation is None else 20001 if mutation == "error" else None)
    assert result.value == (value if mutation is None else None)
