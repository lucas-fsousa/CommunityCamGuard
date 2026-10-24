"""Shared synthetic native lifecycle and retired-ingress fixtures, no camera I/O."""

import threading
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock

from _native_fixture import FRAGMENT, INIT

from backend.app.db import registry, source_policy
from backend.app.media.fragmented_mp4 import Mp4Chunk
from backend.app.media.mp4_fanout import Mp4Fanout
from backend.app.media.owned_bindings import OwnedIngressBindings
from backend.app.media.owned_retirement import OwnedRetirementCoordinator, OwnedRun
from backend.app.media.rtsp_ingress import OwnedRtspIngress
from backend.app.media.source_registry import SourceCleanupUnconfirmed
from backend.app.media.viewer_gate import ViewerGate
from backend.app.recording.ingress_gate import RecordingIngressGate


class SyntheticSource:
    def __init__(self, *, ready=True, invalid=False, cleanup_error=False, ignore_cancel=False):
        self.hub = Mp4Fanout()
        if ready:
            self.hub.publish(Mp4Chunk(True, b"invalid MP4" if invalid else INIT))
            self.hub.publish(Mp4Chunk(False, FRAGMENT))
        self.finish = threading.Event()
        self.entered = threading.Event()
        self.closed = threading.Event()
        self.cleanup_error = cleanup_error
        self.ignore_cancel = ignore_cancel

    def run(self, *, cancelled):
        self.entered.set()
        try:
            while not self.finish.wait(0.002):
                if cancelled() and not self.ignore_cancel:
                    return "stopped"
        finally:
            self.hub.close()
            self.closed.set()
            if self.cleanup_error:
                raise SourceCleanupUnconfirmed("synthetic cleanup uncertainty")

    def subscribe(self):
        return self.hub.subscribe()


def retired_native_transition(*, mac="aa:bb:cc:dd:ee:03", viewers=None):
    registry.init_db()
    camera = registry.upsert_camera(mac)
    pending = source_policy.begin_change(camera.camera_id, "owned_rtsp", expected_revision=0)
    selected = source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    bindings = OwnedIngressBindings()
    viewers = viewers if viewers is not None else ViewerGate()
    recordings = RecordingIngressGate(lock=threading.Lock(), retire=lambda _: None, has_process=lambda _: False)
    coordinator = OwnedRetirementCoordinator(viewers=viewers, recordings=recordings, bindings=bindings)
    old = OwnedRtspIngress(camera.camera_id, "rtsp://127.0.0.1:9/synthetic", binary=Path(__file__))
    old._process = Mock(pid=12345)
    old._url = "rtsp://127.0.0.1:12345/ingress_" + "a" * 32
    bindings.attach(old)
    done = Mock(spec=Future)
    done.result.side_effect = lambda **kwargs: setattr(old, "_process", None)
    receipt = coordinator.retire(OwnedRun(old, done), target_mode="native", expected_revision=selected.revision)
    return camera, coordinator, receipt, viewers, recordings
