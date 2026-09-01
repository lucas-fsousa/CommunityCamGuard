from __future__ import annotations

from backend.app.drivers.yoosee.p2p import av_session
from backend.app.drivers.yoosee.p2p.contracts import CallingAttempt, CallingResult
from backend.app.drivers.yoosee.p2p.media_protocol import (
    build_av_control,
    build_kcp_ack,
    build_kcp_push,
    parse_kcp_segments,
)
from backend.app.drivers.yoosee.p2p.stream_protocol import encrypt_media_tlv


def _calling() -> CallingResult:
    attempt = CallingAttempt(0x123456, 0x89ABCDEF, bytes.fromhex("aa17cd6974f58b1e"))
    return CallingResult(
        True,
        True,
        3,
        True,
        None,
        ("198.51.100.9", 32100),
        18,
        attempt.link_id,
        attempt,
    )


def test_av_initialization_acknowledges_pushes_and_reads_codec(monkeypatch) -> None:
    calling = _calling()
    attempt = calling.attempt
    peer = calling.peer_endpoint
    assert attempt is not None and peer is not None
    high_conv = attempt.link_id | 0x80000000
    low_conv = attempt.link_id
    camera_header = bytes.fromhex(
        "ff ff ff 88 08 01 00 00 04 02 00 01 80 3e 00 00 "
        "00 04 05 0f 80 02 00 00 68 01 00 00"
    )
    replies = (
        (build_kcp_ack(high_conv, 0, 7, unacknowledged=1), peer),
        (build_kcp_push(low_conv, 2, build_av_control(attempt.call_id, 2), timestamp=8), peer),
        (
            build_kcp_push(
                low_conv,
                3,
                encrypt_media_tlv(camera_header, attempt.cookie),
                timestamp=9,
            ),
            peer,
        ),
    )
    calls = 0
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class FakeSocket:
        def sendto(self, payload, address):
            sent.append((payload, address))

    def receive(*_args):
        nonlocal calls
        calls += 1
        return iter(replies if calls == 1 else ())

    monkeypatch.setattr(av_session, "receive_datagrams", receive)
    result = av_session.initialize_av_session(FakeSocket(), calling, 0.1)  # type: ignore[arg-type]

    assert result.accepted is True
    assert result.kcp_ack_count == 1
    assert result.actions == (2,)
    assert result.bulk_frames == 1
    assert result.stream_version == 1
    assert result.encoding_header is not None
    assert result.encoding_header.audio_sample_rate == 16_000
    assert (result.encoding_header.video_width, result.encoding_header.video_height) == (640, 360)
    assert result.inbound_next == ((low_conv, 4),)
    assert result.next_send_sequence == 1
    init_segments = parse_kcp_segments(sent[0][0])
    assert init_segments[0].conv == high_conv
    assert init_segments[0].body[:4] == b"\x03\x02\x4c\x00"
    assert len(sent) == 3  # one accepted INIT plus ACKs for two camera PUSH frames


def test_av_initialization_fails_closed_without_private_route() -> None:
    calling = CallingResult(True, True, 1, True, None, ("198.51.100.9", 32100), 4, 7)
    assert av_session.initialize_av_session(object(), calling, 0.1) == av_session.AvSessionResult(
        0, (), 0, 0, (), None, None
    )
