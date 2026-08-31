from __future__ import annotations

from backend.app.drivers.yoosee.p2p import modern_intercom_session
from backend.app.drivers.yoosee.p2p.av_session import AvSessionResult
from backend.app.drivers.yoosee.p2p.contracts import CallingAttempt, CallingResult
from backend.app.drivers.yoosee.p2p.media_protocol import build_kcp_ack, parse_kcp_segments
from backend.app.drivers.yoosee.p2p.stream_protocol import (
    V1EncodingHeader,
    decrypt_command_tlv,
    decrypt_media_tlv,
)


def _state() -> tuple[CallingResult, AvSessionResult]:
    attempt = CallingAttempt(0x123456, 0x89ABCDEF, bytes.fromhex("aa17cd6974f58b1e"))
    calling = CallingResult(
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
    header = V1EncodingHeader(0x0108, 4, 2, 1, 16, 16_000, 1024, 5, 15, 640, 360)
    av = AvSessionResult(2, (2, 6), 1, 4, ((attempt.link_id, 7),), 1, header)
    return calling, av


def test_modern_lifecycle_separates_media_and_command_channels(monkeypatch) -> None:
    calling, av = _state()
    attempt = calling.attempt
    peer = calling.peer_endpoint
    assert attempt is not None and peer is not None
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class FakeSocket:
        def sendto(self, payload, address):
            sent.append((payload, address))

    def receive(*_args):
        outbound = parse_kcp_segments(sent[-1][0])[0]
        return iter(
            (
                (
                    build_kcp_ack(
                        outbound.conv,
                        outbound.sequence,
                        outbound.timestamp,
                        unacknowledged=outbound.sequence + 1,
                    ),
                    peer,
                ),
            )
        )

    monkeypatch.setattr(modern_intercom_session, "receive_datagrams", receive)
    result = modern_intercom_session.run_modern_intercom_control(
        FakeSocket(), calling, av, 0.1  # type: ignore[arg-type]
    )

    assert result.completed is True
    segments = [parse_kcp_segments(wire)[0] for wire, _peer in sent]
    assert [(item.conv, item.sequence) for item in segments] == [
        (attempt.link_id, 0),
        (attempt.link_id | 0x80000000, 4),
        (attempt.link_id, 1),
        (attempt.link_id | 0x80000000, 5),
        (attempt.link_id, 2),
    ]
    talk_on = decrypt_command_tlv(segments[1].body, attempt.cookie)
    talk_off = decrypt_command_tlv(segments[3].body, attempt.cookie)
    assert talk_on[29] == talk_off[29] == 0x32
    assert talk_on[-1] == 1 and talk_off[-1] == 0
    encoding = decrypt_media_tlv(segments[2].body, attempt.cookie)
    assert encoding[:6] == bytes.fromhex("ff ff ff 88 02 01")
    assert int.from_bytes(segments[0].body[8:12], "little") == 6
    assert int.from_bytes(segments[4].body[8:12], "little") == 7


def test_modern_lifecycle_rejects_an_unexpected_negotiated_codec() -> None:
    calling, av = _state()
    header = av.encoding_header
    assert header is not None
    unsupported = AvSessionResult(
        av.kcp_ack_count,
        av.actions,
        av.bulk_frames,
        av.next_send_sequence,
        av.inbound_next,
        av.stream_version,
        V1EncodingHeader(
            header.marker,
            1,
            header.audio_codec_option,
            header.audio_channels,
            header.audio_bit_width,
            header.audio_sample_rate,
            header.audio_frame_size,
            header.video_codec,
            header.video_frame_rate,
            header.video_width,
            header.video_height,
        ),
    )
    result = modern_intercom_session.run_modern_intercom_control(
        object(), calling, unsupported, 0.1  # type: ignore[arg-type]
    )
    assert result.completed is False
