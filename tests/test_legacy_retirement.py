"""First legacy retirement adapter contract; no production container actions."""

import threading
from unittest.mock import Mock

import pytest

from backend.app.db import registry, source_policy
from backend.app.media.owned_bindings import OwnedIngressBindings
from backend.app.media.owned_retirement import OwnedRetirementCoordinator, RetiredIngress
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from backend.app.media.viewer_gate import ViewerGate
from backend.app.recording.ingress_gate import RecordingIngressGate


def setup():
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:03")
    source_policy.init_db()
    viewers = ViewerGate()
    recordings = RecordingIngressGate(lock=threading.Lock(), retire=lambda _: None, has_process=lambda _: False)
    coordinator = OwnedRetirementCoordinator(viewers=viewers, recordings=recordings, bindings=OwnedIngressBindings())
    return camera, coordinator, viewers, recordings


def test_deployment_callback_runs_only_after_gates_and_transition_then_receipt_is_single_use():
    camera, coordinator, viewers, recordings = setup()
    def close():
        assert viewers.is_drained(camera.camera_id) and recordings.is_retired(camera.camera_id)
        assert source_policy.read_for([camera.camera_id])[camera.camera_id].phase == "transition"
    receipt = coordinator.retire_legacy(camera.camera_id, expected_revision=0, retire_ingress=close)
    with pytest.raises(SourceUnavailable), coordinator.claim(RetiredIngress(receipt.camera_id, receipt.selection)):
        pytest.fail("forged receipt accepted")
    with coordinator.claim(receipt):
        pass
    with pytest.raises(SourceUnavailable), coordinator.claim(receipt):
        pytest.fail("receipt replayed")


def test_unknown_physical_closure_blocks_without_issuing_a_receipt_or_retrying():
    camera, coordinator, viewers, recordings = setup()
    close = Mock(side_effect=RuntimeError("not confirmed"))
    with pytest.raises(SourceCleanupUnconfirmed):
        coordinator.retire_legacy(camera.camera_id, expected_revision=0, retire_ingress=close)
    assert not coordinator._receipts and viewers.is_drained(camera.camera_id) and recordings.is_retired(camera.camera_id)
    assert source_policy.read_for([camera.camera_id])[camera.camera_id].phase == "blocked"
    with pytest.raises(SourceUnavailable):
        coordinator.retire_legacy(camera.camera_id, expected_revision=0, retire_ingress=close)
    assert close.call_count == 1


def test_stale_revision_never_invokes_deployment_adapter():
    camera, coordinator, _, _ = setup()
    close = Mock()
    with pytest.raises(SourceUnavailable):
        coordinator.retire_legacy(camera.camera_id, expected_revision=99, retire_ingress=close)
    close.assert_not_called()
