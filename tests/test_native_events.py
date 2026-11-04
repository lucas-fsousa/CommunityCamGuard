import json

from backend.app.media import native_events as events

CAMERA = 'cam_' + '1' * 24


def test_bounded_failure_history_keeps_only_fixed_safe_facts(tmp_path):
    events.configure(tmp_path)
    events.configure(tmp_path)
    events.ended(CAMERA, error='SECRET', evidence_valid=False, details={
        'session_worker': 'stopped', 'consumer_error': 'Mp4OrderError',
        'session_broker_release': True, 'consumer_alive': False,
        'session_connection_failure': 'SECRET', 'payload': 'SECRET',
        'session_tcp_socket_closed': 'SECRET'})
    text = (tmp_path / 'native-events.jsonl').read_text()
    assert 'SECRET' not in text
    data = json.loads(text)
    assert data['error'] == 'unknown' and not data['evidence_valid']
    assert data['facts']['consumer_error'] == 'Mp4OrderError'
    assert data['facts']['session_broker_release'] is True
    handlers = [h for h in events._log.handlers if isinstance(h, events._Handler)]
    assert len(handlers) == 1 and handlers[0].maxBytes == 512*1024
    assert handlers[0].backupCount == 2


def test_failure_logging_cannot_break_retirement_with_an_unwritable_directory(tmp_path):
    path = tmp_path / 'not-a-directory'
    path.write_text('test')
    events.configure(path)
    events.ended(CAMERA, error='TimeoutError', evidence_valid=True, details={})
