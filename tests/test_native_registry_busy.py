"""Actual SQLite lock through driver validation and source admission, no camera I/O."""

import sqlite3
from contextlib import closing, nullcontext
from types import SimpleNamespace

from backend.app.drivers.yoosee.native_lease_recovery import wrap
from backend.app.drivers.yoosee.native_video_source import NativeVideoResult, NativeVideoSource
from backend.app.drivers.yoosee.shared_read_session import SharedReadResult
from backend.app.media import recovering_source
from tests.test_av_route import ENROLLMENT


def test_real_lock_waits_then_revalidates_before_running_source(tmp_path, monkeypatch):
    events = []
    path = tmp_path / 'registry.db'
    with closing(sqlite3.connect(path, timeout=0)) as writer, closing(sqlite3.connect(path, timeout=0)) as reader:
        writer.execute('CREATE TABLE grant_marker (allowed INTEGER)')
        writer.execute('INSERT INTO grant_marker VALUES (1)')
        writer.commit()
        writer.execute('BEGIN EXCLUSIVE')
        def valid():
            return reader.execute('SELECT allowed FROM grant_marker').fetchone()[0] == 1
        def release(_):
            assert events == []  # No source operation while validation is unknown.
            writer.rollback()
        monkeypatch.setattr(recovering_source.time, 'sleep', release)
        source = NativeVideoSource('test', '123')  # Construction is lazy.
        monkeypatch.setattr(source, 'run', lambda **kw:
                            (events.append('run') or NativeVideoResult('stopped', SharedReadResult('stopped'))))
        accounts = SimpleNamespace(source_intent=lambda entry: nullcontext())
        wrapper = wrap(source, camera_id='test', factory=lambda: source, valid=valid,
                       accounts=accounts, entry=ENROLLMENT)
        assert wrapper.run(cancelled=lambda: False).reason == 'stopped'
    assert events == ['run'] and wrapper.restarts == 0
