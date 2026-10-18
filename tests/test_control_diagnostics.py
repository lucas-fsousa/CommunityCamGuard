import json
from types import SimpleNamespace

import pytest

from backend.app import control_diagnostics as diagnostics
from backend.app.drivers.yoosee.p2p import orientation
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError


@pytest.fixture
def sink(tmp_path):
    old = diagnostics._log.handlers[:]
    diagnostics._log.handlers = []
    diagnostics.configure(tmp_path)
    yield tmp_path / "control-events.jsonl"
    for handler in diagnostics._log.handlers:
        handler.close()
    diagnostics._log.handlers = old


def test_persistent_bounded_sink_is_idempotent_and_sanitized(sink):
    diagnostics.configure(sink.parent)
    handlers = [h for h in diagnostics._log.handlers if isinstance(h, diagnostics._SafeHandler)]
    assert len(handlers) == 1
    handler = handlers[0]
    assert handler.maxBytes == 1024 * 1024 and handler.backupCount == 3
    diagnostics.record("secret", stage="secret", outcome="secret", operation="secret", error=True)
    event = json.loads(sink.read_text())
    assert "secret" not in sink.read_text()
    assert event["error_code"] is None and event["at"].endswith("+00:00")
    handler.close()
    diagnostics._log.removeHandler(handler)
    diagnostics.configure(sink.parent)
    diagnostics.record(None, stage="api", outcome="rejected", error=409)
    assert len(sink.read_text().splitlines()) == 2


def test_rotation_and_io_failure_do_not_leak_or_abort(sink, monkeypatch):
    handler = diagnostics._log.handlers[0]
    handler.maxBytes = 300
    for _ in range(12):
        diagnostics.record(None, stage="api", outcome="rejected", error=502)
    assert len(list(sink.parent.glob("control-events.jsonl*"))) == 4
    monkeypatch.setattr(handler, "doRollover", lambda: (_ for _ in ()).throw(OSError("secret")))
    diagnostics.record(None, stage="api", outcome="rejected", error=502)


def test_api_rejection_is_persisted_without_driver_exception(monkeypatch, sink):
    from fastapi import HTTPException, Response

    from backend.app.api import controls
    from backend.app.drivers import ControlOperationError

    def fail(*_):
        raise ControlOperationError("private-token")

    monkeypatch.setattr(controls, "write_control", fail)
    with pytest.raises(HTTPException) as caught:
        controls.write_camera_control(controls.ControlWriteIn(value="inverted"), Response(),
                                      "cam_" + "a" * 24, "orientation")
    assert caught.value.status_code == 502
    event = json.loads(sink.read_text())
    assert event["stage"] == "api" and event["error_code"] == 502
    assert "private-token" not in sink.read_text()


@pytest.mark.parametrize("failure", ["session", "preflight", "write", "readback", None])
def test_orientation_records_exact_stage_and_closes_socket(monkeypatch, sink, failure):
    closed = []
    sock = SimpleNamespace(bind=lambda _: None, close=lambda: closed.append(True))
    monkeypatch.setattr(orientation.socket, "socket", lambda *_: sock)
    def session(*_):
        if failure == "session":
            raise P2PProbeError("private-token")
        return object(), object(), 10
    monkeypatch.setattr(orientation, "open_camera_session", session)
    reads = []
    def read(*_, **kw):
        reads.append(True)
        if failure == "preflight" and len(reads) == 1:
            return SimpleNamespace(error_code=9, value=None)
        value = 1 if len(reads) == 1 or failure == "readback" else 3
        return SimpleNamespace(error_code=0, value={"setVal": {"multiFlip": value}})
    monkeypatch.setattr(orientation, "exchange_model_read", read)
    monkeypatch.setattr(orientation, "exchange_orientation_write", lambda *a, **kw:
                        SimpleNamespace(error_code=7 if failure == "write" else 0,
                                        transport_acknowledged=True))
    monkeypatch.setattr(orientation.time, "sleep", lambda _: None)
    enrollment = SimpleNamespace(camera_id="cam_" + "a" * 24, device_id="7000000002")
    if failure:
        with pytest.raises(P2PProbeError):
            orientation.set_camera_orientation(enrollment, "inverted")
    else:
        assert orientation.set_camera_orientation(enrollment, "inverted").verified
    events = [json.loads(line) for line in sink.read_text().splitlines()]
    assert len(events) == 2 and events[0]["operation"] == events[1]["operation"]
    assert events[-1]["stage"] == (failure or "complete")
    assert events[-1]["outcome"] == ("failed" if failure else "confirmed")
    assert "private-token" not in sink.read_text() and closed == [True]
