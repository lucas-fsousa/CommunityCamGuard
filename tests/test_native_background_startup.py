import time

from _native_fixture import FRAGMENT, INIT
from _native_runtime_fixture import SyntheticSource, retired_native_transition

from backend.app.db import source_policy
from backend.app.media.fragmented_mp4 import Mp4Chunk
from backend.app.media.native_runtime import NativeSourceRuntime
from backend.app.media.source_offer import NativeSourceOffer


def test_slow_camera_keeps_one_pending_owner_and_publishes_when_ready():
    camera, retirement, receipt, viewers, _ = retired_native_transition()
    source = SyntheticSource(ready=False)
    runtime = NativeSourceRuntime(viewers=viewers, restart_checkpoints=True)
    published = []
    offer = NativeSourceOffer(camera.camera_id, 'synthetic/boot', lambda: source, lambda: True)
    try:
        session = runtime.activate(retirement=retirement, receipt=receipt, offer=offer,
            apply_routes=lambda: published.append(True), ready_timeout=0.01, background=True)
        assert source.entered.wait(1)
        time.sleep(0.05)  # Beyond synchronous timeout; no source teardown/replacement.
        assert not source.closed.is_set() and not session.was_published
        assert runtime.sources.camera_ids() == (camera.camera_id,)
        assert source_policy.read_for((camera.camera_id,))[camera.camera_id].phase == 'transition'
        source.hub.publish(Mp4Chunk(True, INIT)); source.hub.publish(Mp4Chunk(False, FRAGMENT))
        until = time.monotonic() + 2
        while not session.was_published and time.monotonic() < until:
            time.sleep(0.01)
        assert session.was_published and published == [True]
        assert source_policy.read_for((camera.camera_id,))[camera.camera_id].phase == 'active'
    finally:
        runtime.stop()
    assert source.closed.is_set()
    assert not next(iter(runtime._activations.values()), None)


def test_shutdown_joins_pending_publisher_without_late_route_publication():
    camera, retirement, receipt, viewers, _ = retired_native_transition()
    source = SyntheticSource(ready=False)
    runtime = NativeSourceRuntime(viewers=viewers)
    published = []
    runtime.activate(retirement=retirement, receipt=receipt,
        offer=NativeSourceOffer(camera.camera_id, 'synthetic/boot', lambda: source, lambda: True),
        apply_routes=lambda: published.append(True), background=True)
    activation = runtime._activations[camera.camera_id]
    assert source.entered.wait(1)
    runtime.stop()
    assert source.closed.is_set() and not published
    assert not activation._publication._thread.is_alive()
