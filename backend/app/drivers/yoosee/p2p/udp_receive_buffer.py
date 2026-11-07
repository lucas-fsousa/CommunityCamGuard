"""Bounded per-account UDP receive headroom, never global kernel settings."""

import logging
import platform
import socket
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)
MIN_REQUEST = 1024 * 1024
MAX_REQUEST = 4 * 1024 * 1024


@dataclass(frozen=True)
class ReceiveBuffer:
    requested: int
    effective: int
    forced: bool


def _force_option() -> int | None:
    value = getattr(socket, 'SO_RCVBUFFORCE', None)
    if type(value) is int:
        return value
    # Linux asm-generic/socket.h; Python does not always export this constant.
    # Do not guess socket option numbers on other OS/ABI combinations.
    if sys.platform == 'linux' and platform.machine().lower() in {
            'x86_64', 'amd64', 'aarch64', 'arm64', 'armv7l', 'i386', 'i686'}:
        return 33
    return None


def configure_receive_buffer(sock: socket.socket, *, streams: int) -> ReceiveBuffer:
    if type(streams) is not int or streams < 1:
        raise ValueError('positive admitted stream count required')
    requested = min(MAX_REQUEST, max(MIN_REQUEST, streams * 256 * 1024))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, requested)
    effective = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    forced = False
    option = _force_option()
    if effective < requested and option is not None:
        try:
            # Uses an ALREADY available capability only. No privilege request,
            # sysctl write, interface change or failure when permission is absent.
            sock.setsockopt(socket.SOL_SOCKET, option, requested)
            forced = True
        except OSError:
            pass
        effective = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    log.log(logging.WARNING if effective < requested else logging.INFO,
        'native shared UDP receive buffer requested=%d effective=%d forced=%s streams=%d',
        requested, effective, forced, streams)
    return ReceiveBuffer(requested, effective, forced)
