"""Ordinary ASGI startup consumes only reviewed clean retirements; no hardware."""

from types import SimpleNamespace

import pytest
from _native_runtime_fixture import SyntheticSource
from fastapi.testclient import TestClient

from backend.app import config, drivers, main
from backend.app.db import native_restart, registry
from backend.app.media.native_runtime import NativeSourceRuntime
from backend.app.media.owned_runtime import OwnedSourceRuntime
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
