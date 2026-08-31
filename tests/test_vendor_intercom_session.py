from __future__ import annotations

import pytest

from backend.app.drivers.yoosee.p2p import audio_sender, intercom_session
from backend.app.drivers.yoosee.p2p.av_session import AvSessionResult
from backend.app.drivers.yoosee.p2p.contracts import CallingAttempt, CallingResult
from backend.app.drivers.yoosee.p2p.media_protocol import (
    build_kcp_ack,
    parse_kcp_segments,
)
from backend.app.drivers.yoosee.p2p.stream_protocol import (
    V1_MAGIC,
    decrypt_media_tlv,
    pack_legacy_capture_header,
    pack_legacy_talk_control,
)

AMR = bytes.fromhex("3c") + bytes(31)


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
    av = AvSessionResult(2, (2, 6), 1, 4, ((attempt.link_id, 7),), 1, None)
    return calling, av


def test_silent_legacy_intercom_acks_every_stage_and_sends_no_audio(monkeypatch) -> None:
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

    monkeypatch.setattr(intercom_session, "receive_datagrams", receive)
    result = intercom_session.run_silent_legacy_intercom_control(
        FakeSocket(),
        calling,
        av,
        0.1,  # type: ignore[arg-type]
    )

    assert result.completed is True
    assert len(sent) == 5
    bodies = [parse_kcp_segments(wire)[0].body for wire, _peer in sent]
    assert bodies[0][0] == 3 and int.from_bytes(bodies[0][8:12], "little") == 6
    assert decrypt_media_tlv(bodies[1], attempt.cookie) == pack_legacy_capture_header()
    assert decrypt_media_tlv(bodies[2], attempt.cookie) == pack_legacy_talk_control(True)
    assert decrypt_media_tlv(bodies[3], attempt.cookie) == pack_legacy_talk_control(False)
    assert bodies[4][0] == 3 and int.from_bytes(bodies[4][8:12], "little") == 7


def test_silent_intercom_always_stops_and_closes_after_start_ack_loss(monkeypatch) -> None:
    calling, av = _state()
    peer = calling.peer_endpoint
    assert peer is not None
    sent: list[tuple[bytes, tuple[str, int]]] = []
    receive_count = 0

    class FakeSocket:
        def sendto(self, payload, address):
            sent.append((payload, address))

    def receive(*_args):
        nonlocal receive_count
        receive_count += 1
        outbound = parse_kcp_segments(sent[-1][0])[0]
        # ACK AV START and header, lose every talk-ON ACK, then ACK cleanup.
        if outbound.sequence == 2:
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

    monkeypatch.setattr(intercom_session, "receive_datagrams", receive)
    result = intercom_session.run_silent_legacy_intercom_control(
        FakeSocket(),
        calling,
        av,
        0.1,  # type: ignore[arg-type]
    )

    assert result.talk_start_acknowledged is False
    assert result.talk_stop_acknowledged is True
    assert result.av_close_acknowledged is True
    bodies = [parse_kcp_segments(wire)[0].body for wire, _peer in sent]
    assert any(body[0] == 4 for body in bodies)
    assert bodies[-1][0] == 3 and int.from_bytes(bodies[-1][8:12], "little") == 7
    assert receive_count >= 7


def test_silent_intercom_rejects_non_v1_session() -> None:
    calling, av = _state()
    unsupported = AvSessionResult(
        av.kcp_ack_count,
        av.actions,
        av.bulk_frames,
        av.next_send_sequence,
        av.inbound_next,
        2,
        av.encoding_header,
    )
    assert intercom_session.run_silent_legacy_intercom_control(
        object(), calling, unsupported, 0.1
    ) == intercom_session.IntercomControlResult(False, False, False, False, False)


def test_legacy_intercom_sends_audio_between_talk_start_and_cleanup(monkeypatch) -> None:
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

    monkeypatch.setattr(intercom_session, "receive_datagrams", receive)
    monkeypatch.setattr(audio_sender, "receive_datagrams", receive)
    monkeypatch.setattr(audio_sender.time, "sleep", lambda *_args: None)
    result = intercom_session.run_legacy_intercom_control(
        FakeSocket(),
        calling,
        av,
        0.1,
        audio_frames=(AMR, AMR),  # type: ignore[arg-type]
    )

    assert result.completed is True
    assert result.audio == intercom_session.LegacyAudioSendResult(2, 2, 2, 5, False)
    segments = [parse_kcp_segments(wire)[0] for wire, _peer in sent]
    assert [segment.sequence for segment in segments] == list(range(7))
    first_audio = decrypt_media_tlv(segments[3].body, attempt.cookie)
    assert first_audio.startswith(V1_MAGIC)
    assert int.from_bytes(first_audio[6:8], "little") == 1
    assert first_audio[-32:] == AMR
    assert decrypt_media_tlv(segments[5].body, attempt.cookie) == pack_legacy_talk_control(False)
    assert int.from_bytes(segments[6].body[8:12], "little") == 7


def test_incremental_intercom_session_keeps_talk_open_between_frames(monkeypatch) -> None:
    calling, av = _state()
    peer = calling.peer_endpoint
    assert peer is not None
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

    monkeypatch.setattr(intercom_session, "receive_datagrams", receive)
    monkeypatch.setattr(audio_sender, "receive_datagrams", receive)
    monkeypatch.setattr(audio_sender.time, "sleep", lambda *_args: None)
    session = intercom_session.LegacyIntercomSession(
        FakeSocket(),
        calling,
        av,
        0.1,
        max_audio_frames=2,
    )  # type: ignore[arg-type]

    assert session.start() is True
    assert len(sent) == 3
    assert session.send_audio_frame(AMR) is True
    assert session.result().audio == intercom_session.LegacyAudioSendResult(
        1, 1, 1, 4, False
    )
    assert len(sent) == 4
    result = session.close()

    assert result.completed is True
    assert [parse_kcp_segments(wire)[0].sequence for wire, _peer in sent] == list(range(6))
    assert session.close() == result
    with pytest.raises(RuntimeError, match="closed"):
        session.send_audio_frame(AMR)


def test_legacy_intercom_audio_ack_loss_aborts_audio_but_still_cleans_up(monkeypatch) -> None:
    calling, av = _state()
    peer = calling.peer_endpoint
    assert peer is not None
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class FakeSocket:
        def sendto(self, payload, address):
            sent.append((payload, address))

    def receive(*_args):
        outbound = parse_kcp_segments(sent[-1][0])[0]
        if outbound.sequence == 3:
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

    monkeypatch.setattr(intercom_session, "receive_datagrams", receive)
    monkeypatch.setattr(audio_sender, "receive_datagrams", receive)
    result = intercom_session.run_legacy_intercom_control(
        FakeSocket(),
        calling,
        av,
        0.1,
        audio_frames=(AMR, AMR),  # type: ignore[arg-type]
    )

    assert result.completed is False
    assert result.audio == intercom_session.LegacyAudioSendResult(2, 1, 0, 4, True)
    segments = [parse_kcp_segments(wire)[0] for wire, _peer in sent]
    assert [segment.sequence for segment in segments[-2:]] == [4, 5]
    assert int.from_bytes(segments[-1].body[8:12], "little") == 7
