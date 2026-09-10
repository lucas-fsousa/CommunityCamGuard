import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee import capability_collector as collector
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    ModelReadResult,
    OnlineDevice,
    P2PProbeError,
)

ENROLLMENT = P2PEnrollment("1234567890", 1, bytes(64), None, "", "", "cam_" + "1" * 24)


@pytest.mark.parametrize("failure", (None, 20001, "timeout", "exception"))
def test_one_session_fixed_read_paths_and_cleanup(monkeypatch, failure):
    opened = []
    calls = []
    closed = []

    class FakeSocket:
        def __enter__(self):
            opened.append(self)
            return self

        def __exit__(self, *_args):
            closed.append(self)

        def bind(self, _address):
            pass

    monkeypatch.setattr(collector.socket, "socket", lambda *_args: FakeSocket())
    monkeypatch.setattr(
        collector,
        "open_camera_session",
        lambda *_args: (
            CertifiedNode(("192.0.2.1", 19800), 1, bytes(32), 0xFFFFFFFF),
            OnlineDevice(1234567890, 1, False, 1, bytes(16)),
            0xFFFFFFFF,
        ),
    )

    def read(
        _sock, _node, _target, path, sequence, timeout, *, retries, deadline, exact_reports_only
    ):
        assert exact_reports_only is True
        calls.append((path, sequence))
        assert timeout == 1.0 and retries == 1
        if failure == "exception":
            raise OSError("test")
        code = None if failure == "timeout" else failure or 0
        return ModelReadResult(True, code, {})

    monkeypatch.setattr(collector, "exchange_model_read", read)
    if failure == "exception":
        with pytest.raises(OSError):
            collector.collect(ENROLLMENT)
    else:
        observations = collector.collect(ENROLLMENT)
        assert len(observations) == (4 if failure is None else 1)
    assert opened == closed and len(opened) == 1
    expected = collector.CAPABILITY_PATHS if failure is None else collector.CAPABILITY_PATHS[:1]
    assert [path for path, _sequence in calls] == list(expected)
    if failure is None:
        assert [seq for _path, seq in calls] == [0xFFFFFFFF, 0, 1, 2]


def test_requires_linked_identity_before_network(monkeypatch):
    def forbidden(*_args):
        raise AssertionError("network opened")

    monkeypatch.setattr(collector.socket, "socket", forbidden)
    with pytest.raises(P2PProbeError, match="linked camera"):
        collector.collect(P2PEnrollment("1234567890", 1, bytes(64), None, "", ""))
