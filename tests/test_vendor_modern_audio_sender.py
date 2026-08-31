from __future__ import annotations

from backend.app.drivers.yoosee.p2p import modern_audio_sender
from backend.app.drivers.yoosee.p2p.media_protocol import build_kcp_ack, parse_kcp_segments
from backend.app.drivers.yoosee.p2p.stream_protocol import decrypt_media_tlv

PEER = ("198.51.100.7", 32100)
CONV = 0x123456
COOKIE = bytes.fromhex("aa17cd6974f58b1e")


def _adts() -> bytes:
    payload = b"aac"
    length = 7 + len(payload)
    return bytes((0xFF, 0xF1, 0x60, 0x40, length >> 3, ((length & 7) << 5) | 0x1F, 0xFC)) + payload


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, payload, address):
        self.sent.append((payload, address))


def test_modern_sender_uses_epoch_microseconds_and_ack_backpressure(monkeypatch) -> None:
    sock = FakeSocket()
    now = 100.0

    def monotonic() -> float:
        return now

    def sleep(duration: float) -> None:
        nonlocal now
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

    monkeypatch.setattr(modern_audio_sender.time, "monotonic", monotonic)
    monkeypatch.setattr(modern_audio_sender.time, "sleep", sleep)
    monkeypatch.setattr(modern_audio_sender.time, "time_ns", lambda: 1_750_000_123_456_000_000)
    monkeypatch.setattr(modern_audio_sender, "receive_datagrams", receive)
    sender = modern_audio_sender.ModernAudioSender(
        sock, PEER, CONV, COOKIE, {}, 7, 0.1, max_frames=2  # type: ignore[arg-type]
    )

    assert sender.send(_adts()) is True
    assert sender.send(_adts()) is True
    assert sender.close().completed is True
    segments = [parse_kcp_segments(wire)[0] for wire, _peer in sock.sent]
    assert [segment.sequence for segment in segments] == [7, 8]
    first = decrypt_media_tlv(segments[0].body, COOKIE)
    assert int.from_bytes(first[20:28], "little") == 1_750_000_123_456_000
    assert first[30:] == _adts()
