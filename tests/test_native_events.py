import json

from backend.app.media import native_events as events

CAMERA = 'cam_' + '1' * 24


def test_parser_failure_preserves_fixed_box_labels_and_code_site_only(tmp_path):
    events.configure(tmp_path)
    events.ended(CAMERA, error='Mp4OrderError', evidence_valid=True, details={
        'consumer_site': 'feed:81', 'consumer_mp4_expected': 'moof',
        'consumer_mp4_actual': 'mfra', 'stderr': 'SECRET'})
    facts = json.loads((tmp_path / 'native-events.jsonl').read_text())['facts']
    assert facts['consumer_site'] == 'feed:81'
    assert facts['consumer_mp4_expected'] == 'moof'
    assert facts['consumer_mp4_actual'] == 'mfra'
    events.ended(CAMERA, error='ValueError', evidence_valid=True, details={
        'consumer_site': '/private/SECRET:80', 'consumer_mp4_expected': 'SECRET',
        'consumer_mp4_actual': []})
    text = (tmp_path / 'native-events.jsonl').read_text()
    assert 'SECRET' not in text


def test_lease_retirement_is_distinct_from_shared_socket_closure(tmp_path):
    events.configure(tmp_path)
    events.ended(CAMERA, error='retry_planned', evidence_valid=True, details={
        'session_scope':'media_lease', 'session_worker':'leased',
        'session_lease_detached':True, 'session_broker_socket_closed':False})
    facts=json.loads((tmp_path/'native-events.jsonl').read_text())['facts']
    assert facts['session_scope']=='media_lease' and facts['session_worker']=='leased'
    assert facts['session_lease_detached'] is True and facts['session_broker_socket_closed'] is False


def test_scope_and_detach_facts_cannot_log_arbitrary_text(tmp_path):
    events.configure(tmp_path)
    events.ended(CAMERA, error='source_returned', evidence_valid=True,
                 details={'session_scope':'SECRET', 'session_lease_detached':'SECRET'})
    text=(tmp_path/'native-events.jsonl').read_text()
    assert 'SECRET' not in text
    facts=json.loads(text)['facts']
    assert 'session_scope' not in facts and 'session_lease_detached' not in facts


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


def test_retry_facts_keep_fixed_reason_and_protocol_code_but_never_payload(tmp_path):
    events.configure(tmp_path)
    events.ended(CAMERA,error='retry_planned',evidence_valid=True,details={
        'retry_reason':'connection','session_connection_failure':'inventory_timeout',
        'session_protocol_code':'mtp_record_length','payload':'SECRET'})
    result=json.loads((tmp_path/'native-events.jsonl').read_text())
    assert result['error']=='retry_planned'
    assert result['facts']['retry_reason']=='connection'
    assert result['facts']['session_protocol_code']=='mtp_record_length'
    assert 'SECRET' not in repr(result)


def test_confirmed_account_end_retry_reason_survives_safe_projection(tmp_path):
    events.configure(tmp_path)
    events.ended(CAMERA, error='retry_planned', evidence_valid=True,
                 details={'retry_reason': 'account_ended'})
    result = json.loads((tmp_path / 'native-events.jsonl').read_text())
    assert result['facts']['retry_reason'] == 'account_ended'


def test_decoder_exit_facts_are_sanitized_and_local_failure_reason_is_preserved(tmp_path):
    events.configure(tmp_path)
    events.ended(CAMERA,error='retry_planned',evidence_valid=True,details={
        'retry_reason':'consumer_failed','decoder_failure':'timestamp_order','decoder_returncode':'1',
        'stderr':'SECRET'})
    result=json.loads((tmp_path/'native-events.jsonl').read_text())
    assert result['facts']['decoder_failure']=='timestamp_order'
    assert result['facts']['decoder_returncode']=='1'
    assert result['facts']['retry_reason']=='consumer_failed'
    assert 'SECRET' not in repr(result)


def test_kcp_counter_ranges_never_accept_arbitrary_text_or_unbounded_numbers(tmp_path):
    events.configure(tmp_path)
    events.ended(CAMERA,error='retry_planned',evidence_valid=True,details={
        'kcp_next_sequence':'4294967295','kcp_pending':'128','kcp_parts':'SECRET',
        'kcp_gap':'128','kcp_bytes':'9'*100})
    result=json.loads((tmp_path/'native-events.jsonl').read_text())['facts']
    assert result['kcp_next_sequence']=='4294967295' and result['kcp_pending']=='128'
    assert 'kcp_parts' not in result and 'kcp_gap' not in result and 'kcp_bytes' not in result


def test_mtp_failure_records_only_bounded_header_numbers(tmp_path):
    events.configure(tmp_path)
    events.ended(CAMERA, error='retry_planned', evidence_valid=True, details={
        'mtp_kind': '80', 'mtp_length': '1501', 'packet': 'SECRET'})
    result = json.loads((tmp_path / 'native-events.jsonl').read_text())['facts']
    assert result['mtp_kind'] == '80' and result['mtp_length'] == '1501'
    assert 'SECRET' not in repr(result)
    events.ended(CAMERA, error='retry_planned', evidence_valid=True, details={
        'mtp_kind': '256', 'mtp_length': '2048'})
    result = json.loads((tmp_path / 'native-events.jsonl').read_text().splitlines()[-1])['facts']
    assert 'mtp_kind' not in result and 'mtp_length' not in result
