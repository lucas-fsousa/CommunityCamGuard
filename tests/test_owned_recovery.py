"""Per-camera replacement and durable retirement; no physical camera traffic."""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from _owned_runtime_fixture import fake_worker, retired_owned_target

from backend.app.db import owned_restart, registry, source_policy
from backend.app.media import go2rtc
from backend.app.media.owned_runtime import OwnedSourceRuntime
from backend.app.media.owned_sockets import loopback_listener
from backend.app.media.source_registry import SourceCleanupUnconfirmed
from backend.app.services import address_recovery
from backend.app.services.owned_rtsp import OwnedRtspService


def test_replacement_reaps_only_target_before_publishing():
    camera, runtime, receipt = retired_owned_target()
    old, _ = fake_worker(camera)
    runtime.activate(receipt, old, apply_routes=lambda: None)
    other, _, other_receipt = retired_owned_target(runtime=runtime, mac="aa:bb:cc:dd:ee:04")
    unaffected, _ = fake_worker(other)
    other_run = runtime.activate(other_receipt, unaffected, apply_routes=lambda: None)
    replacement, entered = fake_worker(camera)
    def publish():
        assert old.pid is None
        assert unaffected.pid is not None and not other_run.completion.done()
        assert runtime.available(other.camera_id)
    try:
        result = runtime.replace(replacement, apply_routes=publish)
        assert entered.is_set() and result.worker is replacement
        assert runtime.available(camera.camera_id) and not owned_restart.pending()
    finally:
        runtime.stop()


def test_uncertain_old_child_cannot_launch_replacement():
    camera, runtime, receipt = retired_owned_target()
    old, _ = fake_worker(camera, cleanup_error=True)
    runtime.activate(receipt, old, apply_routes=lambda: None)
    replacement, entered = fake_worker(camera)
    with pytest.raises(SourceCleanupUnconfirmed):
        runtime.replace(replacement, apply_routes=lambda: pytest.fail("unsafe publication"))
    assert not entered.is_set() and old.pid is not None
    with pytest.raises(SourceCleanupUnconfirmed):
        runtime.stop(timeout=2)


def test_clean_shutdown_checkpoint_restores_once_without_falling_back():
    camera, runtime, receipt = retired_owned_target()
    runtime._restart_checkpoints = True
    worker, _ = fake_worker(camera)
    runtime.activate(receipt, worker, apply_routes=lambda: None)
    runtime.stop()
    checkpoint, = owned_restart.pending()
    fresh = OwnedSourceRuntime(viewers=runtime._viewers, recordings=runtime._recordings,
                               bindings=runtime._bindings, capacity=None)
    service = OwnedRtspService(fresh, Mock())
    replacement, _ = fake_worker(camera)
    service._worker = lambda cid: replacement
    try:
        assert service.restore(SimpleNamespace(abandoned_namespace_retired=False)) == {camera.camera_id: 'running'}
        assert not owned_restart.pending() and fresh.available(camera.camera_id)
        with pytest.raises(source_policy.SourcePolicyConflict):
            owned_restart.consume(checkpoint)
    finally:
        fresh.stop()


def test_owned_route_patch_does_not_restart_hub(tmp_path, monkeypatch):
    camera, runtime, receipt = retired_owned_target()
    registry.upsert_camera(camera_id=camera.camera_id, stream_path='/synthetic', last_ip='192.0.2.3')
    worker, _ = fake_worker(camera)
    runtime.activate(receipt, worker, apply_routes=lambda: None)
    engine = go2rtc.Go2rtc(manage=False, config_path=tmp_path/'hub.json', owned_ingress=runtime._bindings)
    engine.restart = Mock(side_effect=AssertionError("shared hub restarted"))
    response = Mock(status=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    request = Mock(return_value=response)
    monkeypatch.setattr(go2rtc.urllib.request, 'urlopen', request)
    try:
        engine.patch_owned_source(camera.camera_id)
        sent = request.call_args_list[0].args[0]
        assert sent.method == 'PATCH'
        assert parse_qs(urlsplit(sent.full_url).query) == {'name':[camera.camera_id], 'src':[worker.restream_url]}
        assert {parse_qs(urlsplit(call.args[0].full_url).query)['name'][0]
                for call in request.call_args_list} == {camera.camera_id, camera.camera_id+'_hd', camera.camera_id+'_web'}
        rendered = json.loads(engine.config_path.read_text())
        for hd, level in ((True, 'hd'), (False, 'sd')):
            key = f'ccg_owned_{level}_video'
            assert rendered['ffmpeg'][key] == go2rtc.quality.encode_raw_args(
                go2rtc.get_settings().live_quality, go2rtc.get_settings().live_fps, hd=hd).removeprefix('#raw=')
        assert all(not any(char.isspace() for char in rendered['streams'][name])
                   for name in (camera.camera_id+'_hd', camera.camera_id+'_web'))
        engine.restart.assert_not_called()
        selected = source_policy.read_for((camera.camera_id,))[camera.camera_id]
        source_policy.block(camera.camera_id, expected_revision=selected.revision)
        request.reset_mock()
        with pytest.raises(RuntimeError):
            engine.patch_owned_source(camera.camera_id)
        request.assert_not_called()
    finally:
        runtime.stop()


def test_deferred_startup_retries_only_unspent_clean_receipt():
    camera, runtime, receipt = retired_owned_target()
    runtime._restart_checkpoints = True
    worker, _ = fake_worker(camera)
    runtime.activate(receipt, worker, apply_routes=lambda: None)
    runtime.stop()
    fresh = OwnedSourceRuntime(viewers=runtime._viewers, recordings=runtime._recordings,
                               bindings=runtime._bindings)
    media = Mock()
    service = OwnedRtspService(fresh, media)
    replacement, _ = fake_worker(camera)
    service._worker = lambda cid: replacement
    try:
        assert not fresh.reserved(camera.camera_id)
        service.refresh(camera.camera_id)
        assert fresh.available(camera.camera_id) and not owned_restart.pending()
        media.patch_owned_source.assert_called_once_with(camera.camera_id)
    finally:
        fresh.stop()


def test_owned_dhcp_change_and_retry_never_reload_other_sources(monkeypatch):
    registry.init_db()
    camera = registry.upsert_camera('aa:bb:cc:dd:ee:03', last_ip='192.0.2.3', stream_path='/live')
    pending = source_policy.begin_change(camera.camera_id, 'owned_rtsp', expected_revision=0)
    source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    media = Mock(); media.stream_online.return_value = {}
    owned = Mock(); owned.available.return_value = True
    owned.refresh.side_effect = [OSError('synthetic patch failure'), None]
    monkeypatch.setattr(address_recovery, 'find_addresses', lambda *args: {camera.mac:'192.0.2.4'})
    monkeypatch.setattr(address_recovery, 'address_matches', lambda *args: True)
    recovery = address_recovery.AddressRecovery(media, Mock(), owned=owned)
    recovery.tick(); recovery.tick()
    assert registry.get_camera_by_id(camera.camera_id).last_ip == '192.0.2.4'
    assert recovery._owned_pending == {camera.camera_id}
    recovery.tick()
    assert not recovery._owned_pending and owned.refresh.call_count == 2
    media.restart.assert_not_called()


def test_dead_owned_worker_recovers_at_confirmed_address_despite_scan_cooldown(monkeypatch):
    registry.init_db()
    camera = registry.upsert_camera('aa:bb:cc:dd:ee:03', last_ip='192.0.2.3', stream_path='/live')
    pending = source_policy.begin_change(camera.camera_id, 'owned_rtsp', expected_revision=0)
    source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    media = Mock(); media.stream_online.return_value = {}
    owned = Mock(); owned.available.return_value = False
    monkeypatch.setattr(address_recovery, 'address_matches', lambda *args: True)
    lookup = Mock(side_effect=AssertionError('unnecessary subnet scan'))
    monkeypatch.setattr(address_recovery, 'find_addresses', lookup)
    recovery = address_recovery.AddressRecovery(media, Mock(), owned=owned)
    recovery._next_scan = time.monotonic()+300
    recovery.tick(); recovery.tick()
    owned.refresh.assert_called_once_with(camera.camera_id)
    media.restart.assert_not_called()
    lookup.assert_not_called()


@pytest.mark.skipif(sys.platform != 'linux' or not Path('bin/go2rtc').is_file(),
                    reason='pinned Linux go2rtc fixture required')
def test_patch_against_real_pinned_hub_keeps_other_stream_and_process(tmp_path):
    camera, runtime, receipt = retired_owned_target()
    registry.upsert_camera(camera_id=camera.camera_id, stream_path='/synthetic', last_ip='192.0.2.3')
    worker, _ = fake_worker(camera)
    runtime.activate(receipt, worker, apply_routes=lambda: None)
    other = 'cam_' + 'b'*24
    path = tmp_path/'api-only.json'
    path.write_text(json.dumps({'app':{'modules':['api', 'rtsp', 'ffmpeg', 'exec']}, 'api':{'listen':'127.0.0.1:0'},
        'rtsp':{'listen':''},
        'streams':{camera.camera_id:'rtsp://127.0.0.1:9/old', other:'rtsp://127.0.0.1:9/other'},
        'log':{'level':'disabled'}}))
    process = subprocess.Popen([str(Path('bin/go2rtc').resolve()), '-config', str(path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={'PATH':os.defpath, 'GOMAXPROCS':'1', 'GOMEMLIMIT':'64MiB'})
    try:
        port = None
        deadline = time.monotonic()+5
        while process.poll() is None and time.monotonic()<deadline:
            port = loopback_listener(process.pid)
            if port is not None: break
            time.sleep(0.02)
        assert port is not None
        engine = go2rtc.Go2rtc(manage=False, config_path=tmp_path/'generated.json', owned_ingress=runtime._bindings)
        engine.api = f'http://127.0.0.1:{port}'
        def read():
            with urllib.request.urlopen(engine.api+'/api/streams', timeout=2) as response:
                return json.load(response)
        before = read()
        engine.patch_owned_source(camera.camera_id)
        after = read()
        assert worker.restream_url in json.dumps(after[camera.camera_id])
        assert camera.camera_id+'_hd' in after and camera.camera_id+'_web' in after
        assert after[other] == before[other] and process.poll() is None
    finally:
        process.terminate()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=3)
        runtime.stop()
