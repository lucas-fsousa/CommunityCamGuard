from types import SimpleNamespace

import pytest

from backend.app.db import native_restart, registry, source_policy
from backend.app.diagnostics.native_installation import operator_checkpoint


@pytest.mark.parametrize('phase', ['active','blocked','transition'])
@pytest.mark.parametrize('closed', [False,True])
def test_operator_upgrade_requires_actual_namespace_retirement_before_policy_change(phase, closed):
    registry.init_db()
    camera = registry.upsert_camera('aa:bb:cc:dd:ee:03')
    current = source_policy.begin_change(camera.camera_id, 'native', expected_revision=0)
    if phase != 'transition':
        current = source_policy.activate(camera.camera_id, expected_revision=current.revision)
    if phase == 'blocked':
        current = source_policy.block(camera.camera_id, expected_revision=current.revision)
    calls=[]
    def confirm():
        calls.append(True)
        if not closed: raise RuntimeError('old namespace still running')
    containers = SimpleNamespace(confirm_closed=confirm)
    if not closed:
        with pytest.raises(RuntimeError):
            operator_checkpoint(camera.camera_id, 'reviewed/native', containers)
        assert source_policy.read_for((camera.camera_id,))[camera.camera_id] == current
        assert native_restart.pending() == ()
    else:
        checkpoint = operator_checkpoint(camera.camera_id, 'reviewed/native', containers)
        assert native_restart.pending() == (checkpoint,)
        assert checkpoint.revision == current.revision + (phase != 'blocked')
    assert calls == [True]
