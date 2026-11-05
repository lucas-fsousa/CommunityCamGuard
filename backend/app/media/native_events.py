"""Small persistent source-failure history, separate from noisy HTTP access logs."""

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..camera_identity import valid_camera_id
from .decoder_failure import FAILURE_CATEGORIES

_log = logging.getLogger('ccg.native_events')
_errors = frozenset({'TimeoutError', 'Mp4SizeError', 'Mp4OrderError', 'Mp4CapacityError',
    'DecoderExitedError', 'BrokenPipeError', 'OSError', 'ValueError', 'P2PProbeError',
    'SourceUnavailable', 'SourceCleanupUnconfirmed', 'RuntimeError', 'source_returned', 'retry_planned'})
_protocols = frozenset({'kcp_assembly_timeout', 'kcp_invalid_segment', 'kcp_conflicting_duplicate',
    'kcp_receive_budget', 'kcp_fragment_chain', 'tcp_session_timeout', 'av_handshake_timeout',
    'av_control_timeout', 'media_initialization_timeout', 'media_idle_timeout', 'tcp_io_timeout',
    'tcp_eof', 'mtp_record_length', 'av_pending_capacity', 'av_pending_timeout'})
_returncodes = frozenset(str(n) for n in range(-128, 256))
_counters = {'kcp_next_sequence':2**32-1, 'kcp_pending':128, 'kcp_parts':128,
             'kcp_gap':127, 'kcp_remaining':128, 'kcp_bytes':1024*1024,
             'mtp_kind':255, 'mtp_length':2047, 'kcp_window_rejected':2**32-1,
             'kcp_first_rejected':2**32-1, 'kcp_last_rejected':2**32-1}
_labels = frozenset({'stopped', 'protocol', 'socket', 'connection', 'transport', 'busy', 'consumer_failed',
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
    for key in ('consumer_error', 'session_worker', 'session_connection_failure', 'session_protocol_code', 'retry_reason'):
        value = details.get(key, '')
        facts[key] = value if isinstance(value, str) and value in (_errors | _labels | _protocols) else 'unknown'
    cause = details.get('decoder_failure')
    facts['decoder_failure'] = cause if isinstance(cause, str) and cause in FAILURE_CATEGORIES else 'unknown'
    code = details.get('decoder_returncode')
    facts['decoder_returncode'] = code if isinstance(code, str) and code in _returncodes else 'unknown'
    for key, maximum in _counters.items():
        value = details.get(key)
        if isinstance(value,str) and value.isascii() and value.isdigit() and len(value)<=10 and int(value)<=maximum:
            facts[key] = value
    _log.warning(json.dumps({'at': datetime.now(UTC).isoformat(timespec='milliseconds'),
        'camera_id': camera_id, 'error': error if error in _errors else 'unknown',
        'evidence_valid': evidence_valid is True, 'facts': facts}, separators=(',', ':')))
