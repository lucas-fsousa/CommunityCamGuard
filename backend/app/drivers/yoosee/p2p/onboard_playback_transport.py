"""Fail-closed boundary for the not-yet-certified Yoosee playback transport."""

from __future__ import annotations

from typing import NoReturn

from .contracts import P2PProbeError


def require_runtime_playback_read_certified() -> NoReturn:
    """Reject live SD-card queries before any socket or camera session is opened.

    Request codecs plus the broker and known-LAN B9 carriers are recovered. Camera 3 acknowledged
    exact V2 requests on both routes but returned no application page, so production execution
    remains disabled until authoritative platform-version or firmware-support evidence explains
    the semantic silence and a bounded listing returns a valid page.
    """

    raise P2PProbeError("Yoosee onboard playback transport is not runtime-certified")
