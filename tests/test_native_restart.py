"""Durable native retirement is one-use, exact-generation and driver-neutral."""

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import Mock

import pytest
from _native_runtime_fixture import SyntheticSource, retired_native_transition

from backend.app.db import native_restart, source_policy
from backend.app.media.native_runtime import NativeSourceRuntime
from backend.app.media.owned_bindings import OwnedIngressBindings
from backend.app.media.owned_retirement import OwnedRetirementCoordinator
from backend.app.media.source_offer import NativeSourceOffer
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from backend.app.media.viewer_gate import ViewerGate
from backend.app.recording.ingress_gate import RecordingIngressGate


def coordinator():
    viewers=ViewerGate()
    recordings=RecordingIngressGate(lock=threading.Lock(),retire=lambda _:None,has_process=lambda _:False)
    return OwnedRetirementCoordinator(viewers=viewers,recordings=recordings,bindings=OwnedIngressBindings())


def completed(*,cleanup_error=False):
    camera,retirement,receipt,viewers,_=retired_native_transition()
    runtime=NativeSourceRuntime(viewers=viewers,restart_checkpoints=True)
    source=SyntheticSource(cleanup_error=cleanup_error)
    offer=NativeSourceOffer(camera.camera_id,'synthetic/one',lambda:source,lambda:True)
    session=runtime.activate(retirement=retirement,receipt=receipt,offer=offer,apply_routes=lambda:None)
    if cleanup_error:
        with pytest.raises(SourceCleanupUnconfirmed): runtime.stop(timeout=3)
    else:
        runtime.stop(timeout=3)
    return camera,runtime,session


def test_intent_and_blocked_policy_alone_cannot_create_restart_authority():
    camera,_,receipt,_,_=retired_native_transition()
    blocked=source_policy.block(camera.camera_id,expected_revision=receipt.selection.revision)
    assert native_restart.pending()==()
    with pytest.raises(source_policy.SourcePolicyConflict):
        native_restart.consume(native_restart.CleanRestart(camera.camera_id,blocked.revision,'synthetic/one'))


def test_clean_shutdown_can_restore_with_new_runtime_and_issues_next_generation():
    camera,old,session=completed()
    checkpoint,=native_restart.pending()
    assert old._stopped and session.was_published and session._closed
    assert not old._executor._jobs and all(not t.is_alive() for t in old._executor._threads)
    assert checkpoint.revision==session.selection.revision
    retirement=coordinator()
    current=NativeSourceRuntime(viewers=retirement.viewer_gate,restart_checkpoints=True)
    source=SyntheticSource()
    offer=NativeSourceOffer(camera.camera_id,checkpoint.domain,lambda:source,lambda:True)
    try:
        restarted=current.restore(retirement=retirement,checkpoint=checkpoint,offer=offer,apply_routes=lambda:None)
        assert native_restart.pending()==() and source.entered.is_set()
        assert restarted.selection.phase=='active' and restarted.selection.revision==checkpoint.revision+2
        assert current.sources.state(camera.camera_id)=='running'
        with pytest.raises(SourceUnavailable):
            current.restore(retirement=retirement,checkpoint=checkpoint,offer=offer,apply_routes=lambda:None)
    finally:
        current.stop(timeout=3)
    next_checkpoint,=native_restart.pending()
    assert next_checkpoint.revision==checkpoint.revision+3
    with pytest.raises(source_policy.SourcePolicyConflict): native_restart.consume(checkpoint)


def test_uncertain_source_shutdown_never_publishes_a_clean_checkpoint():
    camera,runtime,_=completed(cleanup_error=True)
    assert native_restart.pending()==() and not runtime._stopped
    assert runtime.sources.state(camera.camera_id)=='blocked'


@pytest.mark.parametrize('invalid',['domain','camera','evidence'])
def test_failed_review_cannot_consume_checkpoint_or_construct_source(invalid):
    camera,_,_=completed()
    checkpoint,=native_restart.pending()
    retirement=coordinator()
    current=NativeSourceRuntime(viewers=retirement.viewer_gate,restart_checkpoints=True)
    factory=Mock(side_effect=AssertionError('no source birth'))
    offer=NativeSourceOffer('cam_'+'f'*24 if invalid=='camera' else camera.camera_id,
        'other/domain' if invalid=='domain' else checkpoint.domain,factory,lambda:invalid!='evidence')
    with pytest.raises(SourceUnavailable):
        current.restore(retirement=retirement,checkpoint=checkpoint,offer=offer,apply_routes=lambda:None)
    factory.assert_not_called()
    assert native_restart.pending()==(checkpoint,)
    current.stop(timeout=3)


def test_changed_generation_invalidates_old_clean_receipt():
    completed()
    checkpoint,=native_restart.pending()
    with pytest.raises(source_policy.SourcePolicyConflict):
        native_restart.consume(replace(checkpoint,revision=checkpoint.revision+1))
    assert native_restart.pending()==(checkpoint,)
    native_restart.consume(checkpoint)
    assert native_restart.pending()==()


def test_checkpoint_is_spent_atomically_by_at_most_one_caller():
    completed()
    checkpoint,=native_restart.pending()
    barrier=threading.Barrier(2)
    def claim():
        barrier.wait(timeout=2)
        try: return native_restart.consume(checkpoint).phase
        except source_policy.SourcePolicyConflict: return 'denied'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(claim) for _ in range(2)]
        assert sorted(f.result(timeout=3) for f in futures)==['denied','transition']


def test_failed_factory_does_not_restore_the_spent_checkpoint():
    camera,_,_=completed()
    checkpoint,=native_restart.pending()
    retirement=coordinator()
    current=NativeSourceRuntime(viewers=retirement.viewer_gate,restart_checkpoints=True)
    factory=Mock(side_effect=RuntimeError('synthetic constructor failure'))
    offer=NativeSourceOffer(camera.camera_id,checkpoint.domain,factory,lambda:True)
    with pytest.raises(SourceUnavailable):
        current.restore(retirement=retirement,checkpoint=checkpoint,offer=offer,apply_routes=lambda:None)
    assert native_restart.pending()==() and factory.call_count==1
    with pytest.raises(SourceCleanupUnconfirmed): current.stop(timeout=3)
    assert native_restart.pending()==() and not current._stopped
