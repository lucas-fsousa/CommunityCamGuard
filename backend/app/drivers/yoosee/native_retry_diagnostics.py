"""Fixed, bounded failure facts before retry discards the previous source instance."""

import logging
from typing import Protocol

from ...media import native_events
from .native_video_source import NativeVideoResult

log = logging.getLogger(__name__)


class DiagnosticSource(Protocol):
    def diagnostics(self) -> dict[str, str | bool]: ...


def record(camera_id: str, result: NativeVideoResult, source: DiagnosticSource) -> None:
    try:
        details = source.diagnostics()
        details['retry_reason'] = result.reason
        native_events.ended(camera_id, error='retry_planned', evidence_valid=True, details=details)
    except Exception:
        # A full disk/diagnostic failure cannot change retirement or retry policy.
        log.warning('native retry diagnostic unavailable camera_id=%s', camera_id)
