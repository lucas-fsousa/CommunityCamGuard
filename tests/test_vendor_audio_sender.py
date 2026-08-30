from __future__ import annotations

import pytest

from backend.app.drivers.yoosee.p2p import audio_sender
from backend.app.drivers.yoosee.p2p.media_protocol import (
    KCP_ACK,
    build_kcp_ack,
    build_kcp_push,
    parse_kcp_segments,
)

PEER = ("198.51.100.7", 32100)
CONV = 0x123456
COOKIE = bytes.fromhex("aa17cd6974f58b1e")
AMR = bytes.fromhex("3c") + bytes(31)


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, payload, address):
        self.sent.append((payload, address))


def test_sender_paces_frames_and_requires_each_ack(monkeypatch) -> None:
    sock = FakeSocket()
    now = 100.0
    sleeps: list[float] = []

    def monotonic() -> float:
        return now

    def sleep(duration: float) -> None:
        nonlocal now
        sleeps.append(duration)
        now += duration

    def receive(*_args):
        outbound = parse_kcp_segments(sock.sent[-1][0])[0]
        return iter(
            (
                (
                    build_kcp_ack(
                        outbound.conv,
                        outbound.sequence,
                        outbound.timestamp,
                        unacknowledged=outbound.sequence + 1,
                    ),
                    PEER,
                ),
            )
        )

    monkeypatch.setattr(audio_sender.time, "monotonic", monotonic)
    monkeypatch.setattr(audio_sender.time, "sleep", sleep)
    monkeypatch.setattr(audio_sender, "receive_datagrams", receive)

    result = audio_sender.send_legacy_audio_frames(
        sock,
        PEER,
        CONV,
        COOKIE,
        {},
        7,
        (AMR, AMR, AMR),
        0.1,  # type: ignore[arg-type]
    )

    assert result == audio_sender.LegacyAudioSendResult(3, 3, 3, 10, False)
    assert result.completed is True
    assert sleeps == pytest.approx([0.02, 0.02])
    outbound = [parse_kcp_segments(wire)[0] for wire, _peer in sock.sent]
    assert [segment.sequence for segment in outbound] == [7, 8, 9]
    assert all(segment.command != KCP_ACK for segment in outbound)


def test_sender_aborts_queue_after_bounded_ack_loss(monkeypatch) -> None:
    sock = FakeSocket()
    monkeypatch.setattr(audio_sender, "receive_datagrams", lambda *_args: iter(()))

    result = audio_sender.send_legacy_audio_frames(
        sock,
        PEER,
        CONV,
        COOKIE,
        {},
        3,
        (AMR, AMR),
        0.1,  # type: ignore[arg-type]
    )

    assert result == audio_sender.LegacyAudioSendResult(2, 1, 0, 4, True)
    assert result.completed is False
    assert len(sock.sent) == 3


def test_sender_acks_camera_push_while_waiting_for_audio_ack(monkeypatch) -> None:
    sock = FakeSocket()
    camera_push = build_kcp_push(CONV, 11, b"camera", timestamp=45)

    def receive(*_args):
        outbound = parse_kcp_segments(sock.sent[0][0])[0]
        return iter(
            (
                (camera_push, PEER),
                (
                    build_kcp_ack(
                        outbound.conv,
                        outbound.sequence,
                        outbound.timestamp,
                        unacknowledged=outbound.sequence + 1,
                    ),
                    PEER,
                ),
            )
        )

    monkeypatch.setattr(audio_sender, "receive_datagrams", receive)
    result = audio_sender.send_legacy_audio_frames(
        sock,
        PEER,
        CONV,
        COOKIE,
        {},
        2,
        (AMR,),
        0.1,  # type: ignore[arg-type]
    )

    assert result.completed is True
    response = parse_kcp_segments(sock.sent[1][0])[0]
    assert response.command == KCP_ACK
    assert (response.sequence, response.unacknowledged) == (11, 12)


@pytest.mark.parametrize(
    "frames",
    [(), (b"\x3c",), (bytes.fromhex("34") + bytes(31),), (AMR,) * 501],
)
def test_sender_rejects_invalid_or_unbounded_audio(frames) -> None:
    with pytest.raises(ValueError):
        audio_sender.validate_legacy_audio_frames(frames)
