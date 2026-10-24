"""A timed join alone is not a background-service retirement receipt."""

import threading

from ..media.source_registry import SourceCleanupUnconfirmed


def join_thread(thread: threading.Thread | None, *, timeout: float) -> None:
    if thread is not None:
        if thread.ident is None and not thread.is_alive():
            return  # Thread.start failed; no worker exists to join
        thread.join(timeout=timeout)


def require_retired(thread: threading.Thread | None) -> None:
    if thread is not None and thread.is_alive():
        raise SourceCleanupUnconfirmed("background service thread retirement unconfirmed")
