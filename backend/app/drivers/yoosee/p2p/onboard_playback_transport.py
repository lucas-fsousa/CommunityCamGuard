"""Fail-closed boundary for the unrecovered Yoosee playback transport."""

from __future__ import annotations

from typing import NoReturn

from .contracts import P2PProbeError


def require_runtime_playback_read_certified() -> NoReturn:
    """Reject live SD-card queries before any socket or camera session is opened.

    The request codecs are statically recovered, but the current SDK sends
    these messages over an established ``Connection``/``StreamPipe`` channel.
    A bare brokered B9 experiment caused visible LED activity, so that route
    has been removed rather than retained as unreachable transport code.
    """

    raise P2PProbeError("Yoosee onboard playback transport is not runtime-certified")
