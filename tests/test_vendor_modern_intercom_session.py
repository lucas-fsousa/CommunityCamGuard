from __future__ import annotations

from backend.app.drivers.yoosee.p2p import modern_intercom_session
from backend.app.drivers.yoosee.p2p.av_session import AvSessionResult
from backend.app.drivers.yoosee.p2p.contracts import CallingAttempt, CallingResult
from backend.app.drivers.yoosee.p2p.media_protocol import build_kcp_ack, parse_kcp_segments
from backend.app.drivers.yoosee.p2p.stream_protocol import (
    V1EncodingHeader,
    decrypt_command_tlv,
    decrypt_media_tlv,
    encrypt_command_tlv,
    unpack_v1_sequence_user_data,
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
        responses = [
            (
                build_kcp_ack(
                    outbound.conv,
                    outbound.sequence,
                    outbound.timestamp,
                    unacknowledged=outbound.sequence + 1,
                ),
                peer,
            )
        ]
        if outbound.conv == attempt.link_id | 0x80000000 and outbound.sequence == 4:
            command = decrypt_command_tlv(outbound.body, attempt.cookie)
            assert unpack_v1_sequence_user_data(command)[1] == 0x32
            responses.append(
                (
                    modern_intercom_session.build_kcp_push(
                        outbound.conv,
                        11,
                        encrypt_command_tlv(command, attempt.cookie),
                    ),
                    peer,
                )
            )
        return iter(responses)

    monkeypatch.setattr(modern_intercom_session, "receive_datagrams", receive)
    result = modern_intercom_session.run_modern_intercom_control(
        FakeSocket(), calling, av, 0.1  # type: ignore[arg-type]
    )

    assert result.completed is True
    segments = [
        segment
        for wire, _peer in sent
        for segment in parse_kcp_segments(wire)
        if segment.command == modern_intercom_session.KCP_PUSH
    ]
    assert [(item.conv, item.sequence) for item in segments] == [
        (attempt.link_id, 0),
        (attempt.link_id, 1),
        (attempt.link_id | 0x80000000, 4),
        (attempt.link_id | 0x80000000, 5),
        (attempt.link_id, 2),
    ]
    talk_on = decrypt_command_tlv(segments[2].body, attempt.cookie)
    talk_off = decrypt_command_tlv(segments[3].body, attempt.cookie)
    assert talk_on[29] == talk_off[29] == 0x32
    assert talk_on[-1] == 1 and talk_off[-1] == 0
    encoding = decrypt_media_tlv(segments[1].body, attempt.cookie)
    assert encoding == bytes.fromhex(
        "ff ff ff 88 02 01 00 00 05 00 00 01 40 1f 00 00 "
        "a0 00 00 00 00 00 00 00 00 00 00 00"
    )
    assert int.from_bytes(segments[0].body[8:12], "little") == 6
    assert int.from_bytes(segments[4].body[8:12], "little") == 7


def test_modern_lifecycle_does_not_reuse_the_negotiated_receive_codec(monkeypatch) -> None:
    calling, av = _state()
    peer = calling.peer_endpoint
    assert peer is not None
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
    sent = []

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
        FakeSocket(), calling, unsupported, 0.1  # type: ignore[arg-type]
    )
    assert result.completed is True


def test_modern_lifecycle_uses_transport_ack_without_command_response(
    monkeypatch,
) -> None:
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

    assert result.talk_start_acknowledged is True
    assert result.header_acknowledged is True
    pushes = [
        segment
        for wire, _peer in sent
        for segment in parse_kcp_segments(wire)
        if segment.command == modern_intercom_session.KCP_PUSH
    ]
    assert [(item.conv, item.sequence) for item in pushes] == [
        (attempt.link_id, 0),
        (attempt.link_id, 1),
        (attempt.link_id | 0x80000000, 4),
        (attempt.link_id | 0x80000000, 5),
        (attempt.link_id, 2),
    ]


def test_modern_start_fails_when_the_microphone_command_is_not_delivered(monkeypatch) -> None:
    calling, av = _state()
    attempt = calling.attempt
    peer = calling.peer_endpoint
    assert attempt is not None and peer is not None
    sent = []

    class FakeSocket:
        def sendto(self, payload, address):
            sent.append((payload, address))

    def receive(*_args):
        outbound = parse_kcp_segments(sent[-1][0])[0]
        if outbound.conv == attempt.link_id | 0x80000000 and outbound.sequence == 4:
            return iter(())
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
    session = modern_intercom_session.ModernIntercomSession(
        FakeSocket(), calling, av, 0.1  # type: ignore[arg-type]
    )

    assert session.start() is False
    result = session.close()
    assert result.talk_start_acknowledged is False
    assert result.talk_stop_acknowledged is True
    assert result.av_close_acknowledged is True
