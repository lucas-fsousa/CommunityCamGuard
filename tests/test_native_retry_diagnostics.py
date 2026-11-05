from backend.app.drivers.yoosee import native_retry_diagnostics as diagnostics
from backend.app.drivers.yoosee.native_video_source import NativeVideoResult, NativeVideoSource
from backend.app.drivers.yoosee.shared_read_session import SharedReadResult


def test_retry_records_diagnostics_before_the_source_instance_is_discarded(monkeypatch):
    source=NativeVideoSource('cam_'+'1'*24,'123')
    source._session._connection_failure='inventory_timeout'
    calls=[]
    monkeypatch.setattr(diagnostics.native_events,'ended',lambda *a,**kw:calls.append((a,kw)))
    diagnostics.record('cam_'+'1'*24,NativeVideoResult('connection',SharedReadResult('connection')),source)
    assert calls[0][1]['error']=='retry_planned'
    assert calls[0][1]['details']['retry_reason']=='connection'
    assert calls[0][1]['details']['session_connection_failure']=='inventory_timeout'


def test_broken_retry_logging_cannot_abort_the_recovery_loop(monkeypatch):
    source=NativeVideoSource('cam_'+'1'*24,'123')
    def broken(*a,**k):raise OSError('full disk')
    monkeypatch.setattr(diagnostics.native_events,'ended',broken)
    diagnostics.record('cam_'+'1'*24,NativeVideoResult('connection',SharedReadResult('connection')),source)


def test_real_wrapper_observes_retired_attempt_before_replacement(monkeypatch):
    from backend.app.drivers.yoosee import native_recovery
    camera_id='cam_'+'1'*24
    source=NativeVideoSource(camera_id,'123')
    source._session._connection_failure='socket_timeout'
    result=NativeVideoResult('connection',SharedReadResult('connection'))
    monkeypatch.setattr(source,'run',lambda **_:result)
    stopped=[]
    def observed(cid,outcome,current):
        assert cid==camera_id and outcome is result and current is source
        stopped.append(True)
    monkeypatch.setattr(native_recovery,'record_retry',observed)
    recovered=native_recovery.wrap(source,camera_id=camera_id,factory=lambda:None,valid=lambda:True)
    assert recovered.run(cancelled=lambda:bool(stopped)) is result
    assert stopped==[True]
