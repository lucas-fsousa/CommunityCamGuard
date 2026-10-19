"""Orientation must use its exact field and current selected-device reads."""
from types import SimpleNamespace

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.p2p import orientation as module
from backend.app.drivers.yoosee.p2p.contracts import (
    ModelReadResult,
    ModelWriteResult,
    P2PProbeError,
)


@pytest.mark.parametrize("payload", [
    1, 3, True, {"t": 1}, {"setVal": {"nightViewMode": 1}},
    {"setVal": {"brightness": 3}}, {"nested": {"multiFlip": 1}},
    {"setVal": {"nested": {"multiFlip": 3}}}, {"multiFlip": 3, "setVal": {}},
    {"setVal": {"multiFlip": True}}, {"setVal": {"multiFlip": 3.0}},
])
def test_other_fields_never_prove_orientation(payload):
    assert module.extract_orientation(payload) is None


@pytest.mark.parametrize("value", [1, 3])
@pytest.mark.parametrize("wrapped", [False, True])
def test_exact_field_wins_over_other_scalars(value, wrapped):
    fields = {"nightViewMode": 3 if value == 1 else 1, "multiFlip": value}
    assert module.extract_orientation({"setVal": fields} if wrapped else fields) == value


@pytest.mark.parametrize("wrong_target", [False, True])
def test_exact_target_and_correlated_preflight_and_delayed_readback(monkeypatch, wrong_target):
    reads, writes, closed = [], [], []
    enrollment = P2PEnrollment("7000000002", 123, bytes(64), None, "", "")
    sock = SimpleNamespace(bind=lambda _: None, close=lambda: closed.append(True))
    monkeypatch.setattr(module.socket, "socket", lambda *_: sock)
    monkeypatch.setattr(module, "open_camera_session", lambda *_: (
        object(), SimpleNamespace(device_id=7000000003 if wrong_target else 7000000002), 40,
    ))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    def read(*args, **kwargs):
        assert not wrong_target, "read sent to wrong target"
        assert kwargs.get("require_correlated_response") is True
        reads.append(args[4])
        return ModelReadResult(True, 0, {"setVal": {"multiFlip": 3 if len(reads) == 3 else 1}})

    def write(*args, **kwargs):
        assert not wrong_target, "write sent to wrong target"
        writes.append(args[3])
        return ModelWriteResult(True, 0)

    monkeypatch.setattr(module, "exchange_model_read", read)
    monkeypatch.setattr(module, "exchange_orientation_write", write)
    if wrong_target:
        with pytest.raises(P2PProbeError, match="identity mismatch"):
            module.set_camera_orientation(enrollment, "inverted")
        assert reads == writes == []
    else:
        assert module.set_camera_orientation(enrollment, "inverted").verified
        assert reads == [40, 42, 43] and writes == ["inverted"]
    assert closed == [True]
