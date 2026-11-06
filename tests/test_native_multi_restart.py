"""Bounded generic restart admission; fake media only, no camera/network I/O."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from _native_runtime_fixture import SyntheticSource, retired_native_transition

from backend.app import drivers
from backend.app.db import native_restart
from backend.app.media.native_runtime import NativeSourceRuntime
from backend.app.media.source_offer import NativeSourceOffer
from backend.app.media.source_registry import SourceUnavailable
from backend.app.services.native_startup import restore_reviewed
from tests.test_native_restart import coordinator


def checkpoints(domains):
    cameras = []
    for index, domain in enumerate(domains):
        camera, retirement, receipt, viewers, _ = retired_native_transition(
            mac=f"aa:bb:cc:dd:ee:{index + 3:02x}")
        runtime = NativeSourceRuntime(viewers=viewers, restart_checkpoints=True)
        source = SyntheticSource()
        runtime.activate(retirement=retirement, receipt=receipt,
            offer=NativeSourceOffer(camera.camera_id, domain, lambda source=source: source, lambda: True),
            apply_routes=lambda: None)
        runtime.stop(timeout=3)
        cameras.append(camera)
    return cameras, {item.camera_id: item for item in native_restart.pending()}


@pytest.mark.parametrize("capacity", [1, 3])
def test_startup_restores_up_to_capacity_without_spending_other_checkpoints(monkeypatch, capacity):
    cameras, pending = checkpoints(("fake/one", "fake/two", "fake/three"))
    retirement = coordinator()
    runtime = NativeSourceRuntime(viewers=retirement.viewer_gate, capacity=capacity, restart_checkpoints=True)
    sources = {camera.camera_id: SyntheticSource() for camera in cameras}
    factories = {cid: Mock(return_value=source) for cid, source in sources.items()}
    def offered(camera, *, archive):
        cid = camera.camera_id
        return NativeSourceOffer(cid, pending[cid].domain, factories[cid], lambda: True)
    monkeypatch.setattr(drivers, "for_camera", lambda _: SimpleNamespace(native_media_offer=offered))
    try:
        statuses = restore_reviewed(runtime, retirement, SimpleNamespace(native_archive=lambda _: None), lambda: None)
        active = {cid for cid, status in statuses.items() if status == "running"}
        assert len(active) == capacity and set(runtime.sources.camera_ids()) == active
        unspent = {item.camera_id: item for item in native_restart.pending()}
        assert unspent == {cid: checkpoint for cid, checkpoint in pending.items() if cid not in active}
        for cid, factory in factories.items():
            assert factory.call_count == (1 if cid in active else 0)
            if cid not in active:
                assert statuses[cid] == "admission_unavailable"
    finally:
        runtime.stop(timeout=3)
    assert len(native_restart.pending()) == 3


def test_same_driver_domain_is_reserved_before_spending_checkpoint_or_constructing_source():
    cameras, pending = checkpoints(("fake/shared", "fake/shared"))
    retirement = coordinator()
    runtime = NativeSourceRuntime(viewers=retirement.viewer_gate, capacity=3, restart_checkpoints=True)
    first, second = cameras
    source, denied = SyntheticSource(), Mock(side_effect=AssertionError("must not construct"))
    try:
        runtime.restore(retirement=retirement, checkpoint=pending[first.camera_id],
            offer=NativeSourceOffer(first.camera_id, "fake/shared", lambda: source, lambda: True),
            apply_routes=lambda: None)
        offer = NativeSourceOffer(second.camera_id, "fake/shared", denied, lambda: True)
        assert not runtime.can_admit(offer)
        with pytest.raises(SourceUnavailable, match="admission"):
            runtime.restore(retirement=retirement, checkpoint=pending[second.camera_id], offer=offer,
                            apply_routes=lambda: None)
        assert native_restart.pending() == (pending[second.camera_id],)
        denied.assert_not_called()
    finally:
        runtime.stop(timeout=3)


def test_concurrent_restore_cannot_spend_capacity_while_first_source_is_preparing():
    cameras, pending = checkpoints(("fake/one", "fake/two"))
    retirement = coordinator()
    runtime = NativeSourceRuntime(viewers=retirement.viewer_gate, restart_checkpoints=True)
    first, second = cameras
    source, denied = SyntheticSource(ready=False), Mock(side_effect=AssertionError("must not construct"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        preparing = pool.submit(runtime.restore, retirement=retirement, checkpoint=pending[first.camera_id],
            offer=NativeSourceOffer(first.camera_id, "fake/one", lambda: source, lambda: True),
            apply_routes=lambda: None)
        try:
            assert source.entered.wait(1)
            with pytest.raises(SourceUnavailable, match="admission"):
                runtime.restore(retirement=retirement, checkpoint=pending[second.camera_id],
                    offer=NativeSourceOffer(second.camera_id, "fake/two", denied, lambda: True),
                    apply_routes=lambda: None)
            assert native_restart.pending() == (pending[second.camera_id],)
            denied.assert_not_called()
        finally:
            runtime.stop(timeout=3)
        with pytest.raises(SourceUnavailable):
            preparing.result(timeout=2)
