import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee import capability_collector as collector
from backend.app.drivers.yoosee.p2p.contracts import (
    CertifiedNode,
    ModelReadResult,
    OnlineDevice,
    P2PProbeError,
)
from backend.app.drivers.yoosee.p2p.white_light import WhiteLightExchange

ENROLLMENT = P2PEnrollment("1234567890", 1, bytes(64), None, "", "", "cam_" + "1" * 24)


@pytest.mark.parametrize("failure", (None, 20001, "timeout", "exception"))
@pytest.mark.parametrize("include_siren", (False, True))
def test_one_session_fixed_read_paths_and_cleanup(monkeypatch, failure, include_siren):
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
        _sock,
        _node,
        _target,
        path,
        sequence,
        timeout,
        *,
        retries,
        deadline,
        require_correlated_response,
    ):
        assert require_correlated_response is True
        calls.append((path, sequence))
        assert timeout == 1.0 and retries == 1
        if failure == "exception":
            raise OSError("test")
        code = None if failure == "timeout" else failure or 0
        return ModelReadResult(True, code, {})

    monkeypatch.setattr(collector, "exchange_model_read", read)
    light_calls = []

    def light_read(*args, **kwargs):
        light_calls.append((args, kwargs))
        return WhiteLightExchange(False, False, None)

    monkeypatch.setattr(collector, "exchange_white_light", light_read)
    if failure == "exception":
        with pytest.raises(OSError):
            collector.collect(ENROLLMENT, include_siren=include_siren)
    else:
        observations = collector.collect(ENROLLMENT, include_siren=include_siren)
        assert len(observations) == (6 + int(include_siren) if failure is None else 1)
    assert opened == closed and len(opened) == 1
    expected = collector.CAPABILITY_PATHS if failure is None else collector.CAPABILITY_PATHS[:1]
    if failure is None and include_siren:
        expected += ("Action.expelCtrl",)
    assert [path for path, _sequence in calls] == list(expected)
    if failure is None:
        assert [seq for _path, seq in calls] == [0xFFFFFFFF, *range(5 + int(include_siren))]
        args, kwargs = light_calls[0]
        assert len(light_calls) == 1 and args[0] is opened[0]
        assert args[4] is None and args[5] == 5 + int(include_siren)
        assert kwargs["require_correlated_response"] is True and kwargs["retries"] == 1
    else:
        assert not light_calls


def test_requires_linked_identity_before_network(monkeypatch):
    def forbidden(*_args):
        raise AssertionError("network opened")

    monkeypatch.setattr(collector.socket, "socket", forbidden)
    with pytest.raises(P2PProbeError, match="linked camera"):
        collector.collect(P2PEnrollment("1234567890", 1, bytes(64), None, "", ""))
