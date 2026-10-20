"""Serialize broker-using control operations, including the PTZ release boundary."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from .contracts import P2PProbeError

_channel = threading.RLock()
_cleanup_uncertain = False
_waiters_lock = threading.Lock()
_waiters = 0
_local = threading.local()
WAIT_SECONDS = 15.0


class ControlChannelBusy(P2PProbeError):
    """No channel ownership was obtained; no operation was started."""


def control_channel_contended() -> bool:
    """Advisory signal for an existing owner to drain/close BEFORE yielding.

    Does not release the lock, authenticate, or guarantee waiter ordering. A
    future persistent owner may use it as cancellation input; never skip STOP.
    Reentrant acquisition by the owner is not considered competing demand.
    """
    with _waiters_lock:
        return _waiters > 0


def block_uncertain_channel() -> None:
    """Trusted owner only: no further broker login until process replacement.

    Experimental handoff uses this when cleanup cannot be proven. There is no
    reset API, automatic retry or attempt to reclaim an uncertain socket.
    """
    global _cleanup_uncertain
    with _channel:
        _cleanup_uncertain = True


@contextmanager
def own_control_channel(*, wait: bool = True) -> Iterator[None]:
    """No retry: another login must not invalidate a route between START and STOP.

    Reentrant because native PTZ owns the channel across its one-shot preparation
    helper and subsequent motion. A conservative driver-wide lock also covers
    refreshed access IDs; it is not a queue of repeatable movement commands.
    Background preparation uses wait=False: it must not queue behind an active
    owner or signal contention that would drain a future persistent media owner.
    Already waiting foreground callers take precedence over background admission.
    """
    global _waiters
    if type(wait) is not bool:
        raise ValueError("explicit channel wait policy required")
    depth = getattr(_local, "depth", 0)
    if not wait:
        # Only a nonblocking acquire may run under this bookkeeping lock.
        with _waiters_lock:
            acquired = False if not depth and _waiters else _channel.acquire(timeout=0)
    else:
        if not depth:
            with _waiters_lock:
                _waiters += 1
        try:
            acquired = _channel.acquire(timeout=WAIT_SECONDS)
        finally:
            if not depth:
                with _waiters_lock:
                    _waiters -= 1
    if not acquired:
        raise ControlChannelBusy("another broker control operation is still running")
    _local.depth = depth + 1
    try:
        if _cleanup_uncertain:
            raise ControlChannelBusy("broker cleanup unconfirmed; authentication blocked")
        yield
    finally:
        _local.depth = depth
        _channel.release()
