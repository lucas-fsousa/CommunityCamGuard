"""High-frequency camera status reads cannot depend on GC to release SQLite FDs."""

import gc
import sys
from pathlib import Path

import pytest

from backend.app.db import p2p, registry


@pytest.mark.skipif(sys.platform != "linux", reason="procfs descriptor accounting")
def test_repeated_status_reads_close_connections_even_when_gc_is_disabled():
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:03")
    p2p.init_db()
    gc.collect()
    before = len(list(Path('/proc/self/fd').iterdir()))
    enabled = gc.isenabled()
    gc.disable()
    try:
        for _ in range(100):
            registry.list_cameras()
            registry.get_camera(camera.mac)
            registry.get_camera_by_id(camera.camera_id)
            p2p.has_enrollment_for_camera(camera.camera_id)
            p2p.get_enrollment_for_camera(camera.camera_id)
        assert len(list(Path('/proc/self/fd').iterdir())) <= before + 2
    finally:
        if enabled:
            gc.enable()
        gc.collect()
