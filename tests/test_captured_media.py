import struct

import pytest

from backend.app.drivers.yoosee.p2p.contracts import CallingAttempt
from backend.app.drivers.yoosee.p2p.rendezvous_protocol import build_direct_calling_request
from backend.app.drivers.yoosee.p2p.stream_protocol import encrypt_media_tlv
from scripts.captured_media import CallingKey, CapturedMedia, CapturedSessions
from tests.test_vendor_media_session import _route

CLIENT = ("192.0.2.1", 34567)
CAMERA = ("192.0.2.2", 50000)


def handshake(cookie=b"12345678"):
    node, device, attempt, _ = _route()
    attempt = CallingAttempt(attempt.link_id, attempt.call_id, cookie)
    return attempt, build_direct_calling_request(node, 123, device, CLIENT[0], CLIENT[1], attempt, 1)


def control(call_id):
    body = bytearray(76)
    body[:4] = b"\x03\x00\x4c\x00"
    struct.pack_into("<I", body, 4, call_id)
    struct.pack_into("<I", body, 8, 2)
    return bytes(body)


def header():
    body = bytearray(28)
    body[:4] = bytes.fromhex("ffffff88")
    struct.pack_into("<H", body, 4, 264)
    body[8:12] = bytes((4, 2, 0, 1))
    struct.pack_into("<IHBBII", body, 12, 16000, 1024, 5, 15, 1920, 1080)
    return bytes(body)


def test_binding_requires_exact_endpoints_link_and_checksum():
    sessions = CapturedSessions()
    attempt, wire = handshake()
    sessions.observe(CLIENT, CAMERA, wire)
    key = sessions.lookup(CAMERA, CLIENT, attempt.link_id)
    assert key == CallingKey(attempt.call_id, attempt.cookie)
    assert sessions.lookup(CLIENT, CAMERA, attempt.link_id | 0x80000000) == key
    assert sessions.lookup((CAMERA[0], 50001), CLIENT, attempt.link_id) is None
    assert sessions.lookup(CAMERA, CLIENT, attempt.link_id + 1) is None
    assert "12345678" not in repr(key) and str(attempt.call_id) not in repr(key)
    corrupt = bytearray(wire)
    corrupt[50] ^= 1
    other = CapturedSessions()
    other.observe(CLIENT, CAMERA, bytes(corrupt))
    assert other.lookup(CAMERA, CLIENT, attempt.link_id) is None


def test_conflicting_cookie_fails_closed_even_if_original_repeated():
    sessions = CapturedSessions()
    attempt, wire = handshake()
    sessions.observe(CLIENT, CAMERA, wire)
    sessions.observe(CLIENT, CAMERA, handshake(b"87654321")[1])
    sessions.observe(CLIENT, CAMERA, wire)
    assert sessions.lookup(CAMERA, CLIENT, attempt.link_id) is None


def test_complete_tlv_decryption_header_spans_messages():
    inspector = CapturedMedia()
    key = CallingKey(123, b"12345678")
    inspector.consume(control(123), key)
    for part in (header()[:13], header()[13:]):
        inspector.consume(encrypt_media_tlv(part, key.cookie), key)
    result = inspector.report
    assert result["decoded_messages"] == 2 and result["decoded_bytes"] == 28
    assert result["encoding_header"]["video_width"] == 1920
    assert result["encoding_header"]["audio_sample_rate"] == 16000
    assert bytes(inspector._prefix) == header()


@pytest.mark.parametrize("case", ["missing", "call", "changed", "flags", "no_control"])
def test_never_decrypt_with_unproven_binding(case):
    inspector = CapturedMedia()
    key = CallingKey(123, b"12345678")
    candidate = None if case == "missing" else key
    if case != "no_control":
        inspector.consume(control(124 if case == "call" else 123), candidate)
    if case == "changed":
        candidate = CallingKey(123, b"87654321")
    message = encrypt_media_tlv(header(), key.cookie)
    if case == "flags":
        message = message[:1] + b"\x80" + message[2:]
    inspector.consume(message, candidate)
    assert inspector.report["decoded_messages"] == 0
    assert inspector.report["skipped_media"] == 1


def test_no_midstream_magic_scan():
    key = CallingKey(123, b"12345678")
    inspector = CapturedMedia()
    inspector.consume(control(123), key)
    inspector.consume(encrypt_media_tlv(b"garbage" * 4 + header(), key.cookie), key)
    assert inspector.report["encoding_header"] is None
    assert len(inspector._prefix) == 28


def test_wrong_cookie_does_not_produce_expected_encoding_header():
    inspector = CapturedMedia()
    key = CallingKey(123, b"12345678")
    inspector.consume(control(123), key)
    inspector.consume(encrypt_media_tlv(header(), b"87654321"), key)
    assert inspector.report["encoding_header"] is None
