"""Best-effort media datagrams; never used for broker or physical controls.

KCP and AV readiness/idle deadlines own loss recovery. A full local send buffer
must not turn a dropped ACK into retirement of the shared account. No queue,
sleep, socket reopening or application-command replay is introduced here.
"""

import errno
from typing import Literal

from .broker_read_socket import ReadSocket

SendOutcome = Literal['sent', 'timeout', 'would_block', 'no_buffers']


def send_media(sock: ReadSocket, wire: bytes, peer: tuple[str, int]) -> SendOutcome:
    try:
        sent = sock.sendto(wire, peer)
    except TimeoutError:
        return 'timeout'
    except OSError as error:
        if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            return 'would_block'
        if error.errno == errno.ENOBUFS:
            return 'no_buffers'
        raise  # Route/descriptor and unknown failures are NOT congestion.
    if sent != len(wire):
        raise OSError('partial UDP media datagram')
    return 'sent'


def socket_error_label(error: BaseException) -> tuple[str, int | None]:
    """Fixed local classification, never exception text or packet/peer details."""
    name = type(error).__name__
    name = name if name in {'TimeoutError', 'BlockingIOError', 'OSError'} else 'unknown'
    number = getattr(error, 'errno', None)
    return name, number if type(number) is int and 0 <= number <= 4095 else None
