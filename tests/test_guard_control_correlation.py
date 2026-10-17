"""Both guard controls must bind preflight/readback to the selected session."""
import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.contracts import WeeklySchedule
from backend.app.drivers.yoosee.p2p import smart_protection as master
from backend.app.drivers.yoosee.p2p import smart_protection_schedule as plan
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    ModelReadResult,
    ModelWriteResult,
    OnlineDevice,
    P2PProbeError,
)

ENTRY = P2PEnrollment("7000000002", 123, bytes(64), None, "", "")
OLD = WeeklySchedule("08:00", "18:00", ("mon",))
NEW = WeeklySchedule("09:00", "18:00", ("mon",))


@pytest.mark.parametrize("payload", [
    0, 1, True, {"setVal": 1}, {"guardParm": {"enable": 1}},
    {"setVal": {"plan": {"enable": 1}}},
    {"enable": 1, "setVal": {"enable": False}},
])
def test_master_does_not_infer_state_from_other_shapes(payload):
    assert master.extract_smart_protection_enabled(payload) is None


@pytest.mark.parametrize("schedule", [False, True])
@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("wrong_target", [False, True])
def test_guard_operations_correlate_all_reads_and_reject_other_devices(
    monkeypatch, schedule, write, wrong_target,
):
    module = plan if schedule else master
    suffix = "_schedule" if schedule else ""
    reader = getattr(module, "read_camera_smart_protection" + suffix)
    setter = getattr(module, "set_camera_smart_protection" + suffix)
    closed, reads, writes = [], [], []

    class Socket:
        def bind(self, _address):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr(module.socket, "socket", lambda *_args: Socket())
    monkeypatch.setattr(module, "open_camera_session", lambda *_args: (
        CertifiedNode(("192.0.2.1", 19800), 9, bytes(32), 17),
        OnlineDevice(7000000003 if wrong_target else 7000000002, 1, False, 1, bytes(16)), 40))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    def read(*args, **kwargs):
        assert not wrong_target
        assert kwargs["require_correlated_response"] is True
        reads.append(args[4])
        changed = len(reads) >= 3
        value = ({"plan": plan.native_smart_protection_schedule(NEW if changed else OLD)}
                 if schedule else {"enable": 0 if changed else 1})
        return ModelReadResult(True, 0, {"setVal": value})

    def send(*args, **kwargs):
        assert not wrong_target
        writes.append(args[3])
        return ModelWriteResult(True, 0)

    monkeypatch.setattr(module, "exchange_model_read", read)
    monkeypatch.setattr(module, "exchange_smart_protection" + suffix + "_write", send)

    def run():
        return setter(ENTRY, NEW if schedule else False) if write else reader(ENTRY)

    if wrong_target:
        with pytest.raises(P2PProbeError, match="identity mismatch"):
            run()
        assert reads == writes == []
    else:
        result = run()
        if write:
            assert result.verified is True
            assert len(reads) == 3 and len(writes) == 1
        else:
            assert len(reads) == 1 and not writes
    assert closed == [True]
