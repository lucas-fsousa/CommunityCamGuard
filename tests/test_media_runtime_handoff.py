"""Real loopback owned child -> synthetic native source -> dashboard socket."""

import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from _native_fixture import FRAGMENT, INIT
from _native_runtime_fixture import SyntheticSource
from _owned_runtime_fixture import retired_owned_target
from _rtsp_fixture import CameraFixture, describe
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import auth
from backend.app.api import media
from backend.app.db import source_policy
from backend.app.media.native_runtime import NativeSourceRuntime
from backend.app.media.rtsp_ingress import OwnedRtspIngress
from backend.app.media.source_offer import NativeSourceOffer

BINARY = Path(__file__).resolve().parents[1] / "bin/go2rtc"


@pytest.mark.skipif(sys.platform != "linux" or not BINARY.is_file(), reason="pinned Linux go2rtc fixture unavailable")
def test_server_owned_handoff_reaps_rtsp_before_native_factory_and_serves_dashboard(monkeypatch):
    camera, owned, receipt = retired_owned_target()
    native = NativeSourceRuntime(viewers=owned._viewers)
    output = SyntheticSource()
    finished = threading.Event()
    app = FastAPI()
    app.include_router(media.router)
    app.state.native_sources, app.state.viewer_sessions = native.sources, owned._viewers
    monkeypatch.setattr(media.websockets, "connect", lambda *a, **k: pytest.fail("legacy upstream opened"))

    async def application(scope, receive, send):
        try:
            await app(scope, receive, send)
        finally:
            if scope["type"] == "websocket":
                finished.set()

    with CameraFixture() as fixture:
        worker = OwnedRtspIngress(camera.camera_id, fixture.url, binary=BINARY)
        try:
            running = owned.activate(receipt, worker, apply_routes=lambda: None)
            with describe(worker.restream_url), describe(worker.restream_url):
                selected = source_policy.read_for((camera.camera_id,))[camera.camera_id]
                retired = owned.retirement.retire(running, target_mode="native", expected_revision=selected.revision)
                def construct_native():
                    assert worker.pid is None and running.completion.done()
                    assert fixture.closed.wait(2) and fixture.accepted == fixture.peak == 1
                    return output
                factory = Mock(side_effect=construct_native)
                native.activate(retirement=owned.retirement, receipt=retired,
                                offer=NativeSourceOffer(camera.camera_id, "test/integration", factory, lambda: True),
                                apply_routes=lambda: None)
                # Old runtime cleanup must not close the new source's admission.
                owned.stop(timeout=3)
                with TestClient(application) as client:
                    client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
                    with client.websocket_connect(f"/api/go2rtc/ws?src={camera.camera_id}_hd") as socket:
                        request = '{"type":"mse","value":"avc1.640029"}'
                        socket.send_text(request)
                        assert socket.receive_json()["type"] == "mse"
                        assert socket.receive_bytes() == INIT and socket.receive_bytes() == FRAGMENT
                        socket.send_text(request)
                        with pytest.raises(WebSocketDisconnect) as caught:
                            socket.receive_bytes()
                        assert caught.value.code == 1008 and finished.wait(1)
                assert factory.call_count == 1 and owned._recordings.is_retired(camera.camera_id)
                assert fixture.accepted == 1 and native.sources.state(camera.camera_id) == "running"
        finally:
            try:
                native.stop(timeout=5)
            finally:
                owned.stop(timeout=10)
        assert output.closed.is_set() and worker.pid is None
