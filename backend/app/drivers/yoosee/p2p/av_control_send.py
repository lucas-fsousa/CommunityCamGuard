"""Bounded, socket-free reliable transmission of one AV INIT, START or CLOSE.

Not PTZ, intercom, a general KCP sender or a live session initializer. The owner
must gate START on correlated ACCEPT and combine this with the receive coordinator.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .media_protocol import (
    build_av_control,
    build_av_init,
)
from .reliable_kcp_send import ReliableKcpPush


class ReliableAvControl(ReliableKcpPush):
    """One request: INIT at its caller-owned sequence, START zero or CLOSE one.

At most four transmissions, 250 ms apart, within a two-second absolute deadline.
Retransmit identical bytes/sequence, never manufacture new application requests.
An ACK proves transport receipt only, not acceptance, media readiness or execution.
"""

    _label = "AV control"

    def __init__(self, peer: tuple[str, int], link_id: int, call_id: int, *,
                 action: int, sequence: int = 0,
                 request_user_data: bytes | None = None,
                 _sdk_init_repeat: bool = False,
                 _shared_close: bool = False,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if type(link_id) is not int or not 0 < link_id <= 0xFFFFFF:
            raise ValueError("invalid AV link")
        if type(call_id) is not int or not 0 <= call_id <= 0xFFFFFFFF:
            raise ValueError("invalid AV call")
        if type(action) is not int or action not in (1, 6, 7):
            raise ValueError("only AV INIT/START/CLOSE controls are supported")
        if (type(sequence) is not int or not (sequence == (1 if action == 7 else 0)
                or (action == 1 and _sdk_init_repeat is True and 0 <= sequence < 25)
                or (action == 7 and _shared_close is True and 1 <= sequence <= 0xFFFFFFFF))):
            raise ValueError("invalid AV control sequence")
        if request_user_data is not None and action != 1:
            raise ValueError("AV startup metadata is only valid for INIT")
        conv = link_id | 0x80000000 if action == 1 else link_id
        body = (build_av_init(call_id, request_user_data=request_user_data,
                              connection_type=1 if request_user_data is not None else None)
                if action == 1 else build_av_control(call_id, action))
        super().__init__(peer, conv, sequence, body, clock=clock)
