"""Synthetic owned-worker retirement and running-worker fixtures. No OS signals."""

import threading
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock

from backend.app.db import registry, source_policy
from backend.app.media.owned_bindings import OwnedIngressBindings
from backend.app.media.owned_retirement import OwnedRun
from backend.app.media.owned_runtime import OwnedSourceRuntime
from backend.app.media.rtsp_ingress import OwnedRtspIngress
from backend.app.media.source_registry import SourceCleanupUnconfirmed
from backend.app.media.viewer_gate import ViewerGate
from backend.app.recording.ingress_gate import RecordingIngressGate


def retired_owned_target(*, runtime=None, mac="aa:bb:cc:dd:ee:03"):
    registry.init_db()
    camera = registry.upsert_camera(mac)
    pending = source_policy.begin_change(camera.camera_id, "owned_rtsp", expected_revision=0)
    source_policy.activate(camera.camera_id, expected_revision=pending.revision)
    if runtime is None:
        recordings = RecordingIngressGate(lock=threading.Lock(), retire=lambda _: None, has_process=lambda _: False)
        runtime = OwnedSourceRuntime(viewers=ViewerGate(), recordings=recordings,
                                     bindings=OwnedIngressBindings(capacity=2), capacity=2)
    old = OwnedRtspIngress(camera.camera_id, "rtsp://127.0.0.1:9/old", binary=Path(__file__))
    old._process = Mock(pid=12345)
    old._url = "rtsp://127.0.0.1:12345/ingress_" + "a" * 32
    runtime._bindings.attach(old)
    done = Mock(spec=Future)
    done.result.side_effect = lambda **kwargs: setattr(old, "_process", None)
    receipt = runtime.retirement.retire(OwnedRun(old, done), target_mode="owned_rtsp", expected_revision=pending.revision + 1)
    return camera, runtime, receipt


def fake_worker(camera, *, ready=True, cleanup_error=False):
    worker = OwnedRtspIngress(camera.camera_id, "rtsp://127.0.0.1:9/new", binary=Path(__file__))
    entered = threading.Event()
    def run(*, cancelled):
        worker._process = Mock(pid=54321)
        if ready:
            worker._url = "rtsp://127.0.0.1:54321/ingress_" + "b" * 32
        entered.set()
        while not worker._stop.wait(0.002) and not cancelled():
            pass
        worker._url = None
        if cleanup_error:
            raise SourceCleanupUnconfirmed("synthetic retained child")
        worker._process = None
        return "stopped"
    worker.run = run
    return worker, entered
