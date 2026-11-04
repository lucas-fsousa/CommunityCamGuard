from types import SimpleNamespace

import pytest

from backend.app.config import get_settings
from backend.app.db import native_restart, registry, source_policy
from backend.app.media import owner_scope
from backend.app.media.server_owner import ServerOwner, ServerOwnerBusy
from backend.app.services.abandoned_native import checkpoints

OLD = owner_scope.InitScope('a' * 8 + '-aaaa-aaaa-aaaa-' + 'a' * 12, 123, 10)
NEW = owner_scope.InitScope(OLD.boot, 124, 20)


@pytest.fixture
def setup(monkeypatch):
    registry.init_db()
    camera = registry.upsert_camera('aa:bb:cc:dd:ee:03')
    legacy = registry.upsert_camera('aa:bb:cc:dd:ee:04')
    pending = source_policy.begin_change(camera.camera_id, 'native', expected_revision=0)
    selected = source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    return camera, legacy, selected


@pytest.mark.parametrize('old,new,allowed', [(OLD,NEW,True), (OLD,OLD,False),
    (None,NEW,False), (OLD,None,False)])
def test_only_fenced_previous_namespace_can_restore_missing_runtime(setup, monkeypatch, old, new, allowed):
    camera, legacy, selected = setup
    monkeypatch.setattr(owner_scope, 'current', lambda: old)
    previous = ServerOwner(get_settings().db_path)
    previous.acquire()
    before = previous._path.read_bytes()
    with pytest.raises(ServerOwnerBusy):
        ServerOwner(get_settings().db_path).acquire()
    assert previous._path.read_bytes() == before
    previous.close()
    monkeypatch.setattr(owner_scope, 'current', lambda: new)
    current = ServerOwner(get_settings().db_path)
    current.acquire()
    try:
        assert current.abandoned_namespace_retired is allowed
        checkpoints(current, lambda _: SimpleNamespace(domain='reviewed/domain', valid=lambda: True))
        items = native_restart.pending()
        assert bool(items) is allowed
        if allowed:
            item, = items
            assert item.camera_id == camera.camera_id and item.revision == selected.revision + 1
            assert native_restart.consume(item).phase == 'transition'
            with pytest.raises(source_policy.SourcePolicyConflict):
                native_restart.consume(item)
        assert source_policy.read_for((legacy.camera_id,))[legacy.camera_id].permits_legacy_rtsp
    finally:
        current.close()
    assert not current.abandoned_namespace_retired


@pytest.mark.parametrize('offer', [None, SimpleNamespace(domain='reviewed/domain', valid=lambda: False)])
def test_namespace_death_is_not_a_capability_grant(setup, monkeypatch, offer):
    camera, _, selected = setup
    monkeypatch.setattr(owner_scope, 'current', lambda: OLD)
    previous = ServerOwner(get_settings().db_path); previous.acquire(); previous.close()
    monkeypatch.setattr(owner_scope, 'current', lambda: NEW)
    current = ServerOwner(get_settings().db_path); current.acquire()
    try:
        assert checkpoints(current, lambda _: offer) == {camera.camera_id: 'evidence_unavailable'}
        assert native_restart.pending() == ()
        assert source_policy.read_for((camera.camera_id,))[camera.camera_id] == selected
    finally:
        current.close()


@pytest.mark.parametrize('value', [b'', b'\0', b'{}', b'{"version":true,"scope":{}}', b'x'*2049])
def test_missing_or_malformed_scope_never_certifies_a_dead_namespace(value):
    assert owner_scope.decode(value) is None


def test_scope_roundtrip_contains_no_pid_token_or_camera_payload():
    encoded = owner_scope.encode(OLD)
    assert owner_scope.decode(encoded) == OLD
    assert set(__import__('json').loads(encoded)) == {'version','scope'}
