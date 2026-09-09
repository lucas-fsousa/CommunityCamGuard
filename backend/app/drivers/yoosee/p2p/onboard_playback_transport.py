"""Fail-closed boundary for the not-yet-certified Yoosee playback transport."""

from __future__ import annotations

from typing import NoReturn

from .contracts import P2PProbeError


def require_runtime_playback_read_certified() -> NoReturn:
    """Reject live SD-card queries before any socket or camera session is opened.

    Request codecs and the read-only brokered B9 carrier are statically recovered, but no current
    camera response has yet validated the complete listing exchange. A prior ad-hoc B9 experiment
    caused visible LED activity, so production execution remains disabled until one bounded
    camera-3 listing probe proves the exact carrier and response correlation.
    """

    raise P2PProbeError("Yoosee onboard playback transport is not runtime-certified")
