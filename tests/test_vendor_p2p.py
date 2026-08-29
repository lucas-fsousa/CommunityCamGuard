from __future__ import annotations

import json
import socket
import struct

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import client, orientation
from backend.app.drivers.yoosee.p2p.auth import build_conn_authinfo, parse_conn_authinfo
from backend.app.drivers.yoosee.p2p.crypto import (
    gute_mode0_decrypt,
    gute_mode1_decrypt,
    gute_mode1_encrypt,
    gute_mode1_xor_checksum,
    gute_mode2_decrypt,
    gute_mode2_encrypt,
)
from backend.app.drivers.yoosee.p2p.wire import new_header


def test_connection_auth_round_trip_matches_its_signatures():
    token = bytes(range(64))
    frame = build_conn_authinfo(
        0x1020304050607080,
        token,
        sequence=7,
        nonce=bytes(range(12)),
        header_random15=0x1234,
    )
    parsed = parse_conn_authinfo(frame, token)

    assert parsed.access_id == 0x1020304050607080
    assert parsed.token_prefix == token[:48]
    assert parsed.signature_valid is True
    assert parsed.blob_checksum_valid is True
    assert parsed.frame_checksum_valid is True


def test_mode1_and_mode2_crypto_round_trip():
    mode1 = new_header(0x15, 40, 123, 456, 1 << 16)
    struct.pack_into("<I", mode1, 0x18, 1)
    struct.pack_into("<I", mode1, 0x10, gute_mode1_xor_checksum(mode1))
    wire1 = gute_mode1_encrypt(bytes(mode1))
    assert gute_mode1_decrypt(wire1) == bytes(mode1)

    key = bytes(range(32))
    mode2 = new_header(0xA0, 48, 789, 12, 2 << 16)
    mode2[0] = 0x7E
    struct.pack_into("<I", mode2, 0x10, gute_mode1_xor_checksum(mode2))
    wire2 = gute_mode2_encrypt(bytes(mode2), key)
    assert gute_mode2_decrypt(wire2, key) == bytes(mode2)


def test_parse_init_devices_keeps_identity_and_online_state():
    response = bytearray(0x20 + 2 * 0x1C)
    struct.pack_into("<H", response, 0x18, 1)
    struct.pack_into("<HH", response, 0x1C, 1, 1)
    struct.pack_into("<Q", response, 0x20, 7000000001)
    response[0x28] = 0
    struct.pack_into("<Q", response, 0x3C, 7000000002)
    response[0x44] = 1

    devices = client.parse_init_devices(bytes(response))

    assert [device.device_id for device in devices] == [7000000001, 7000000002]
    assert [bool(device.status) for device in devices] == [False, True]


def test_term_dns_request_contains_only_the_selected_numeric_term():
    node = client.CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    wire = client.build_term_dns(node, "7000000002")
    plain = gute_mode2_decrypt(wire, node.session_key)

    assert plain[:2] == b"\x7e\xdb"
    assert struct.unpack_from("<H", plain, 0x18)[0] == 10
    assert plain[0x1C:0x26] == b"7000000002"


def test_calling_and_nat_frames_contain_only_selected_route_identity():
    node = client.CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    device = client.OnlineDevice(7000000002, 1, False, 1, bytes(16))
    attempt = client.CallingAttempt(0x00FBDD35, 0xEF714F65, bytes.fromhex("aa17cd6974f58b1e"))

    calling = gute_mode2_decrypt(
        client.build_calling_request(
            node,
            0x1020304050607080,
            device,
            "192.0.2.20",
            45678,
            attempt,
            18,
        ),
        node.session_key,
    )
    nat = gute_mode0_decrypt(
        client.build_nat_online(0x1020304050607080, device.device_id, attempt.link_id)
    )

    assert calling[:2] == b"\x7e\xa4"
    assert len(calling) == 177
    assert struct.unpack_from("<Q", calling, 0x28)[0] == device.device_id
    assert calling[0x78:0x80] == attempt.cookie
    assert nat[:2] == b"\x7f\xca"
    assert struct.unpack_from("<I", nat, 0x24)[0] == attempt.link_id


def test_parse_mtp_peer_endpoint_rejects_another_link():
    frame = bytearray(0x64)
    frame[1] = 0xA3
    struct.pack_into("<I", frame, 0x1C, 123)
    struct.pack_into(">H", frame, 0x58, 32100)
    frame[0x60:0x64] = socket.inet_aton("198.51.100.9")

    assert client.parse_mtp_peer_endpoint(bytes(frame), 123) == ("198.51.100.9", 32100)
    assert client.parse_mtp_peer_endpoint(bytes(frame), 124) is None


def test_model_read_builder_is_allowlisted_and_contains_no_write_value():
    node = client.CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    wire = client.build_model_read(node, 7000000002, "ProWritable.videoParm", 18, 19)
    plain = gute_mode2_decrypt(wire, node.session_key)

    assert plain[:2] == b"\x7e\xb7"
    assert struct.unpack_from("<Q", plain, 0x18)[0] == 7000000002
    assert plain[0x26:].rstrip(b"\x00") == b"ProWritable.videoParm"
    with pytest.raises(ValueError, match="allowlist"):
        client.build_model_read(node, 7000000002, "ProWritable.unknown", 18, 19)


def test_orientation_builder_matches_recovered_typed_d2_contract():
    node = client.CertifiedNode(("192.0.2.10", 19800), 9, bytes(range(32)), 17)
    wire = orientation.build_orientation_write(node, 7000000002, "inverted", 18, 19)
    plain = gute_mode2_decrypt(wire, node.session_key)

    assert plain[:2] == b"\x7e\xd2"
    assert plain[0x18] == 2
    assert struct.unpack_from("<I", plain, 0x20)[0] == 19
    assert struct.unpack_from("<H", plain, 0x24)[0] == 1
    assert plain[0x26] == 7
    path_length = plain[0x27]
    json_length = struct.unpack_from("<H", plain, 0x28)[0]
    cursor = 0x2A
    assert struct.unpack_from("<Q", plain, cursor)[0] == 7000000002
    cursor += 8
    assert plain[cursor : cursor + path_length].decode() == orientation.ORIENTATION_PATH
    cursor += path_length + 1
    assert plain[cursor : cursor + json_length] == b"3"
    assert not hasattr(client, "build_model_write")
    assert not hasattr(client, "build_model_action")


def test_orientation_response_requires_matching_application_message():
    response = bytearray(0x36)
    response[:2] = b"\x7e\xd3"
    struct.pack_into("<I", response, 0x30, 19)
    struct.pack_into("<H", response, 0x34, 0)

    assert orientation.parse_orientation_write_response(bytes(response), 19) == 0
    assert orientation.parse_orientation_write_response(bytes(response), 20) is None


def test_orientation_values_are_strict_and_reject_before_network(monkeypatch):
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network opened")),
    )
    enrollment = P2PEnrollment(
        device_id="7000000002",
        access_id=123,
        access_token=bytes(range(64)),
        dev_token=None,
        created_at="now",
        updated_at="now",
    )
    with pytest.raises(ValueError, match="normal or inverted"):
        orientation.set_camera_orientation(enrollment, "mirror")


def test_model_read_response_requires_the_selected_device():
    value = {"setVal": {"multiFlip": 1}, "t": 1787545850}
    encoded = json.dumps(value, separators=(",", ":")).encode()
    response = bytearray(0x28 + len(encoded))
    response[:2] = b"\x7e\xb8"
    struct.pack_into("<Q", response, 0x18, 7000000002)
    response[0x20] = 1
    struct.pack_into("<H", response, 0x24, 0)
    struct.pack_into("<H", response, 0x26, len(encoded))
    response[0x28:] = encoded

    assert client.parse_model_read_response(bytes(response), 7000000002) == (0, value)
    assert client.parse_model_read_response(bytes(response), 7000000001) is None


def test_model_report_preserves_optional_device_destination():
    path = b"ProWritable.videoParm\x00"
    value = b'{"setVal":{"multiFlip":1}}\x00'
    report = bytearray(0x22 + 8 + len(path) + len(value))
    report[:2] = b"\x7e\xaa"
    struct.pack_into("<H", report, 0x1C, 1)
    report[0x1F] = len(path) - 1
    struct.pack_into("<H", report, 0x20, len(value) - 1)
    struct.pack_into("<Q", report, 0x22, 7000000002)
    report[0x2A : 0x2A + len(path)] = path
    report[0x2A + len(path) :] = value

    assert client.parse_model_report(bytes(report)) == (
        7000000002,
        "ProWritable.videoParm",
        {"setVal": {"multiFlip": 1}},
    )


def test_inventory_probe_is_read_only_and_sanitized(monkeypatch):
    enrollment = P2PEnrollment(
        device_id="7000000002",
        access_id=123,
        access_token=bytes(range(64)),
        dev_token="ab" * 64,
        created_at="now",
        updated_at="now",
    )
    calls = []
    observed = {}
    node = client.CertifiedNode(("192.0.2.10", 19800), 1, bytes(32), 2)
    devices = (
        client.OnlineDevice(7000000001, 0, False, 1, bytes(16)),
        client.OnlineDevice(7000000002, 1, False, 1, bytes(16)),
    )

    class FakeSocket:
        def bind(self, address):
            calls.append(("bind", address))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(
        client,
        "obtain_list",
        lambda *_args: (
            [("192.0.2.10", 19800)]
            + [(f"192.0.2.{index}", 19000 + index) for index in range(11, 22)]
        ),
    )

    def fake_establish(_sock, _material, endpoints, _timeout, *, deadline):
        observed["endpoints"] = endpoints
        observed["deadline"] = deadline
        return node, devices, 0

    monkeypatch.setattr(client, "establish_initialized_node", fake_establish)
    monkeypatch.setattr(client, "heartbeat_node", lambda *_args: node)
    monkeypatch.setattr(client, "resolve_term", lambda *_args: True)

    result = client.probe_account_inventory(enrollment)

    assert result.authenticated is True
    assert result.device_count == 2
    assert result.online_count == 1
    assert result.target_visible is True
    assert result.target_online is True
    assert result.target_term_resolved is True
    assert len(observed["endpoints"]) == 8
    assert observed["endpoints"][0] == ("192.0.2.10", 19800)
    assert observed["deadline"] > 0
    assert calls == [("bind", ("", 0)), ("close",)]


def test_route_probe_selects_only_bound_camera_and_sanitizes_peer(monkeypatch):
    enrollment = P2PEnrollment(
        device_id="7000000002",
        access_id=123,
        access_token=bytes(range(64)),
        dev_token=None,
        created_at="now",
        updated_at="now",
    )
    node = client.CertifiedNode(("192.0.2.10", 19800), 1, bytes(32), 2)
    devices = (
        client.OnlineDevice(7000000001, 1, False, 1, bytes(16)),
        client.OnlineDevice(7000000002, 1, False, 1, bytes(16)),
    )
    selected = []

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(client, "obtain_list", lambda *_args: [("192.0.2.10", 19800)])
    monkeypatch.setattr(
        client,
        "establish_initialized_node",
        lambda *_args, **_kwargs: (node, devices, 0),
    )
    monkeypatch.setattr(client, "heartbeat_node", lambda *_args: node)

    def fake_call(_sock, _node, _access_id, device, _timeout, **_kwargs):
        selected.append(device.device_id)
        return client.CallingResult(True, True, 3, True, None, ("198.51.100.9", 32100))

    monkeypatch.setattr(client, "call_device", fake_call)

    result = client.probe_camera_route(enrollment)

    assert selected == [7000000002]
    assert result.direct_handshake is True
    assert result.camera_contacted is True
    assert not hasattr(result, "peer_endpoint")


def test_property_read_selects_only_bound_online_camera(monkeypatch):
    enrollment = P2PEnrollment(
        device_id="7000000002",
        access_id=123,
        access_token=bytes(range(64)),
        dev_token=None,
        created_at="now",
        updated_at="now",
    )
    node = client.CertifiedNode(("192.0.2.10", 19800), 1, bytes(32), 2)
    target = client.OnlineDevice(7000000002, 1, False, 1, bytes(16))
    selected = []

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: FakeSocket())

    def fake_session(_sock, _enrollment, _timeout, _deadline):
        selected.append(("call", target.device_id))
        return node, target, 11

    def fake_read(_sock, _node, device, path, sequence, _timeout, **_kwargs):
        selected.append(("read", device.device_id, path, sequence))
        return client.ModelReadResult(True, 0, {"setVal": {"multiFlip": 1}})

    monkeypatch.setattr(client, "_camera_session", fake_session)
    monkeypatch.setattr(client, "exchange_model_read", fake_read)

    result = client.read_camera_property(enrollment, "ProWritable.videoParm")

    assert selected == [
        ("call", 7000000002),
        ("read", 7000000002, "ProWritable.videoParm", 11),
    ]
    assert result.value == {"setVal": {"multiFlip": 1}}


def test_orientation_change_requires_preflight_d3_and_fresh_readback(monkeypatch):
    enrollment = P2PEnrollment(
        device_id="7000000002",
        access_id=123,
        access_token=bytes(range(64)),
        dev_token=None,
        created_at="now",
        updated_at="now",
    )
    node = client.CertifiedNode(("192.0.2.10", 19800), 1, bytes(32), 2)
    target = client.OnlineDevice(7000000002, 1, False, 1, bytes(16))
    calls = []

    class FakeSocket:
        def bind(self, address):
            calls.append(("bind", address))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(
        orientation,
        "open_camera_session",
        lambda *_args: (node, target, 40),
    )

    reads = iter(
        (
            client.ModelReadResult(True, 0, {"setVal": {"multiFlip": 1}}),
            client.ModelReadResult(True, 0, {"setVal": {"multiFlip": 1}}),
            client.ModelReadResult(True, 0, {"setVal": {"multiFlip": 3}}),
        )
    )

    def fake_read(_sock, _node, device, path, sequence, _timeout, **_kwargs):
        calls.append(("read", device.device_id, path, sequence))
        return next(reads)

    def fake_write(_sock, _node, device, orientation, sequence, _timeout, **_kwargs):
        calls.append(("write", device.device_id, orientation, sequence))
        return client.ModelWriteResult(True, 0)

    monkeypatch.setattr(client, "exchange_model_read", fake_read)
    monkeypatch.setattr(orientation, "exchange_orientation_write", fake_write)
    monkeypatch.setattr(orientation.time, "sleep", lambda _seconds: None)

    result = orientation.set_camera_orientation(enrollment, "inverted")

    assert calls == [
        ("bind", ("", 0)),
        ("read", 7000000002, orientation.ORIENTATION_READ_PATH, 40),
        ("write", 7000000002, "inverted", 41),
        ("read", 7000000002, orientation.ORIENTATION_READ_PATH, 42),
        ("read", 7000000002, orientation.ORIENTATION_READ_PATH, 43),
        ("close",),
    ]
    assert result.previous_value == 1
    assert result.requested_value == 3
    assert result.changed is True
    assert result.transport_acknowledged is True
    assert result.error_code == 0
    assert result.verified is True


def test_orientation_change_is_idempotent_and_never_writes_when_already_set(monkeypatch):
    enrollment = P2PEnrollment(
        device_id="7000000002",
        access_id=123,
        access_token=bytes(range(64)),
        dev_token=None,
        created_at="now",
        updated_at="now",
    )
    node = client.CertifiedNode(("192.0.2.10", 19800), 1, bytes(32), 2)
    target = client.OnlineDevice(7000000002, 1, False, 1, bytes(16))

    class FakeSocket:
        def bind(self, _address):
            pass

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(orientation, "open_camera_session", lambda *_args: (node, target, 8))
    monkeypatch.setattr(
        client,
        "exchange_model_read",
        lambda *_args, **_kwargs: client.ModelReadResult(True, 0, {"setVal": {"multiFlip": 1}}),
    )
    monkeypatch.setattr(
        orientation,
        "exchange_orientation_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("write sent")),
    )

    result = orientation.set_camera_orientation(enrollment, "normal")

    assert result.changed is False
    assert result.verified is True
    assert result.previous_value == result.requested_value == 1
