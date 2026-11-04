"""Ordinary ASGI startup consumes only reviewed clean retirements; no hardware."""

from types import SimpleNamespace

import pytest
from _native_runtime_fixture import SyntheticSource
from fastapi.testclient import TestClient

from backend.app import config, drivers, main
from backend.app.db import native_restart, registry, source_policy
from backend.app.media import owner_scope
from backend.app.media.native_runtime import NativeSourceRuntime
from backend.app.media.owned_runtime import OwnedSourceRuntime
from backend.app.media.server_owner import ServerOwner
from backend.app.media.source_offer import NativeSourceOffer
from backend.app.recording.recorder import Recorder
from tests.test_main_lifespan import _patch_services


@pytest.mark.parametrize('enabled',[False,True])
def test_two_ordinary_lifespans_restore_reviewed_source_and_keep_legacy_excluded(monkeypatch,enabled):
    events=[]
    _patch_services(monkeypatch,events)
    monkeypatch.setattr(main,'NativeSourceRuntime',NativeSourceRuntime)
    monkeypatch.setattr(main,'OwnedSourceRuntime',OwnedSourceRuntime)
    monkeypatch.setattr(main,'Recorder',Recorder)
    monkeypatch.setattr(Recorder,'start',lambda _:events.append('rec.start'))
    sources=[]
    def offered(camera,*,archive):
        assert archive.camera_id==camera.camera_id and archive.segment_seconds==300
        assert events[-1]=='storage.check'
        source=SyntheticSource()
        sources.append(source)
        return NativeSourceOffer(camera.camera_id,'synthetic/boot',lambda:source,lambda:True)
    monkeypatch.setattr(drivers,'for_camera',lambda _:SimpleNamespace(native_media_offer=offered))
    with TestClient(main.app):
        camera=registry.upsert_camera('aa:bb:cc:dd:ee:03')
        runtime=main.app.state.native_runtime
        retirement=main.app.state.owned_runtime.retirement
        receipt=retirement.retire_legacy(camera.camera_id,expected_revision=0,retire_ingress=lambda:None)
        source=SyntheticSource()
        runtime.activate(retirement=retirement,receipt=receipt,
            offer=NativeSourceOffer(camera.camera_id,'synthetic/boot',lambda:source,lambda:True),
            apply_routes=lambda:None)
    checkpoint,=native_restart.pending()
    monkeypatch.setenv('AUTOSTART_SERVICES','true')
    monkeypatch.setenv('NATIVE_RESUME_ON_START','true' if enabled else 'false')
    config.get_settings.cache_clear()
    events.clear()
    with TestClient(main.app):
        if not enabled:
            assert main.app.state.native_startup=={} and not sources
            assert main.app.state.native_runtime.sources.camera_ids()==()
            assert native_restart.pending()==(checkpoint,)
            return
        assert main.app.state.native_startup=={camera.camera_id:'running'}
        assert main.app.state.native_runtime.sources.state(camera.camera_id)=='running'
        assert sources[0].entered.is_set() and native_restart.pending()==()
        assert events.index('storage.check')<events.index('media.write_config')<events.index('rec.start')
        assert main.app.state.rec._procs=={}  # no parallel legacy camera capture
    following,=native_restart.pending()
    assert following.revision==checkpoint.revision+3
    assert sources[0].closed.is_set()


def test_ordinary_startup_restores_fenced_native_intent_without_clean_checkpoint(monkeypatch):
    import time
    events = []
    _patch_services(monkeypatch, events)
    monkeypatch.setattr(main, 'NativeSourceRuntime', NativeSourceRuntime)
    monkeypatch.setattr(main, 'OwnedSourceRuntime', OwnedSourceRuntime)
    monkeypatch.setattr(main, 'Recorder', Recorder)
    monkeypatch.setattr(Recorder, 'start', lambda _: events.append('rec.start'))
    with TestClient(main.app):
        camera = registry.upsert_camera('aa:bb:cc:dd:ee:03')
        pending = source_policy.begin_change(camera.camera_id, 'native', expected_revision=0)
        source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    old = owner_scope.InitScope('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 10, 10)
    monkeypatch.setattr(owner_scope, 'current', lambda: old)
    previous = ServerOwner(config.get_settings().db_path)
    previous.acquire(); previous.close()
    monkeypatch.setattr(owner_scope, 'current', lambda: owner_scope.InitScope(old.boot, 11, 20))
    assert native_restart.pending() == ()
    source = SyntheticSource()
    births = []
    def factory():
        births.append(True)
        return source
    def offered(camera, *, archive):
        return NativeSourceOffer(camera.camera_id, 'synthetic/boot', factory, lambda: True)
    monkeypatch.setattr(drivers, 'for_camera', lambda _: SimpleNamespace(native_media_offer=offered))
    monkeypatch.setenv('AUTOSTART_SERVICES', 'true')
    monkeypatch.setenv('NATIVE_RESUME_ON_START', 'true')
    config.get_settings.cache_clear()
    with TestClient(main.app):
        until = time.monotonic() + 2
        while source_policy.read_for((camera.camera_id,))[camera.camera_id].phase != 'active' and time.monotonic() < until:
            time.sleep(0.01)
        assert source_policy.read_for((camera.camera_id,))[camera.camera_id].phase == 'active'
        assert births == [True] and source.entered.is_set()
        assert main.app.state.native_runtime.sources.camera_ids() == (camera.camera_id,)
        assert main.app.state.rec._procs == {}
    assert source.closed.is_set()
