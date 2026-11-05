"""Fixed, bounded failure facts before retry discards the previous source instance."""

import logging

from ...media import native_events
from .native_video_source import NativeVideoResult, NativeVideoSource

log = logging.getLogger(__name__)


def record(camera_id: str, result: NativeVideoResult, source: NativeVideoSource) -> None:
    try:
        details = source.diagnostics()
        details['retry_reason'] = result.reason
        native_events.ended(camera_id, error='retry_planned', evidence_valid=True, details=details)
    except Exception:
        # A full disk/diagnostic failure cannot change retirement or retry policy.
        log.warning('native retry diagnostic unavailable camera_id=%s', camera_id)
