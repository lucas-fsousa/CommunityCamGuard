from __future__ import annotations

import socket
import time

import pytest

from backend.app.drivers.yoosee.p2p import session_io


def test_receives_large_playback_datagram_without_truncation():
    payload = bytes(range(256)) * 20
    # A local datagram pair exercises kernel truncation without depending on
    # host firewall rules or opening an IP endpoint during offline tests.
    receiver, sender = socket.socketpair(type=socket.SOCK_DGRAM)
    with receiver:
        with sender:
            sender.send(payload)
            packets = session_io.receive_datagrams(
                receiver, time.monotonic() + 1.0, max_datagram_size=0x8000
            )
            assert next(packets)[0] == payload
            packets.close()


class _TimeoutSocket:
    def __init__(self) -> None:
        self.receive_sizes: list[int] = []

    def settimeout(self, _timeout: float) -> None:
        pass

    def recvfrom(self, size: int):
        self.receive_sizes.append(size)
        raise TimeoutError


def test_receive_datagrams_uses_explicit_bounded_buffer(monkeypatch):
    sock = _TimeoutSocket()
    monkeypatch.setattr(session_io.time, "monotonic", lambda: 0.0)

    assert list(
        session_io.receive_datagrams(  # type: ignore[arg-type]
            sock,
            1.0,
            max_datagram_size=0x8000,
        )
    ) == []
    assert sock.receive_sizes == [0x8000]


@pytest.mark.parametrize("size", (511, 65536, True))
def test_receive_datagrams_rejects_unsafe_buffer_size(size: int):
    with pytest.raises(ValueError, match="between 512 and 65535"):
        list(
            session_io.receive_datagrams(  # type: ignore[arg-type]
                _TimeoutSocket(),
                1.0,
                max_datagram_size=size,
            )
        )
