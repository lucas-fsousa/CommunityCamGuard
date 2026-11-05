"""Small persistent source-failure history, separate from noisy HTTP access logs."""

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..camera_identity import valid_camera_id

_log = logging.getLogger('ccg.native_events')
_errors = frozenset({'TimeoutError', 'Mp4SizeError', 'Mp4OrderError', 'Mp4CapacityError',
    'DecoderExitedError', 'BrokenPipeError', 'OSError', 'ValueError', 'P2PProbeError',
    'SourceUnavailable', 'SourceCleanupUnconfirmed', 'RuntimeError', 'source_returned'})
_labels = frozenset({'stopped', 'protocol', 'socket', 'connection', 'transport', 'busy',
    'contended', 'heartbeat_timeout', 'media_complete', 'lifetime', 'unavailable',
    'offline', 'socket_timeout', 'socket_error', 'stale_access', 'access_rejected',
    'transport_or_auth', 'list_dns', 'list_timeout', 'broker_heartbeat',
    'certification_unavailable', 'inventory_timeout', 'inventory_unavailable', 'preparation_budget'})
_bools = frozenset({'session_broker_socket_closed', 'session_broker_release',
    'session_tcp_receipt', 'session_tcp_socket_closed', 'session_motion_started',
    'session_motion_cleanup_confirmed', 'session_siren_cleanup_confirmed', 'consumer_alive', 'cleanup_failed'})


class _Handler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        pass  # Full/unwritable storage cannot abort source retirement.


def configure(directory: Path) -> None:
    path = (directory / 'native-events.jsonl').resolve()
    if any(isinstance(h, _Handler) and h.baseFilename == str(path) for h in _log.handlers):
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler = _Handler(path, maxBytes=512 * 1024, backupCount=2, encoding='utf-8')
    except OSError:
        return
    for old in list(_log.handlers):
        _log.removeHandler(old)
        old.close()
    handler.setFormatter(logging.Formatter('%(message)s'))
    _log.addHandler(handler)
    _log.setLevel(logging.WARNING)
    _log.propagate = False


def ended(camera_id: str, *, error: str, evidence_valid: bool, details: dict) -> None:
    if not valid_camera_id(camera_id):
        return
    facts: dict[str, str | bool] = {
        key: value for key, value in details.items() if key in _bools and type(value) is bool}
    for key in ('consumer_error', 'session_worker', 'session_connection_failure'):
        value = details.get(key, '')
        facts[key] = value if isinstance(value, str) and value in (_errors | _labels) else 'unknown'
    _log.warning(json.dumps({'at': datetime.now(UTC).isoformat(timespec='milliseconds'),
        'camera_id': camera_id, 'error': error if error in _errors else 'unknown',
        'evidence_valid': evidence_valid is True, 'facts': facts}, separators=(',', ':')))
