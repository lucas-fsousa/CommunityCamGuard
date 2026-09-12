"""Preparation contracts: fake sessions only, no camera or socket traffic."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.p2p import ptz_prepare as module
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError

IDENTITY = CapabilityIdentity("123456", "789", "test-model", 1, "1.2", "3.4", "")
ENTRY = P2PEnrollment("123456", 42, b"test", None, "", "", "cam_test")


@pytest.fixture
def prepared(monkeypatch):
    state = SimpleNamespace(closed=0, paths=[], constructed=[], now=10.0, error=0,
                            target=123456, status=1)
    sock = SimpleNamespace(bind=lambda address: None, close=lambda: setattr(state, "closed", state.closed + 1))
    state.values = [dict(productID="789", productModel="test-model", revision=1),
                    dict(swVer="1.2", sdkVer="3.4", hwVer=""),
                    dict(t=1, stVal=dict(ptzInfo=dict(id0_status=7)))]
    monkeypatch.setattr(module, "socket", SimpleNamespace(socket=lambda *args: sock, AF_INET=2, SOCK_DGRAM=2))
    monkeypatch.setattr(module.time, "monotonic", lambda: state.now)
    def session(*args):
        return object(), SimpleNamespace(device_id=state.target, status=state.status), 0xFFFFFFFE
    monkeypatch.setattr(module, "open_camera_session", session)
    def read(sock, node, target, path, sequence, timeout, **kwargs):
        assert kwargs == dict(retries=1, deadline=30.0, require_correlated_response=True)
        assert 0 < timeout <= 2
        state.paths.append((path, sequence))
        return SimpleNamespace(error_code=state.error, transport_acknowledged=True,
                               value=state.values[len(state.paths) - 1])
    monkeypatch.setattr(module, "exchange_model_read", read)
    def route(*args, **kwargs):
        state.constructed.append(kwargs)
        return sock
    monkeypatch.setattr(module, "NativePtzRoute", route)
    return state


def prepare(**kwargs):
    return module.prepare_ptz_route(ENTRY, IDENTITY, camera_id="cam_test", direction="left", **kwargs)


def test_only_three_correlated_reads_transfer_socket_ownership(prepared):
    route = prepare()
    assert prepared.closed == 0
    assert [seq for _, seq in prepared.paths] == [0xFFFFFFFE, 0xFFFFFFFF, 0]
    assert prepared.constructed == [dict(access_id=42, device_id=123456, direction="left", sequence=1)]
    route.close()
    assert prepared.closed == 1


@pytest.mark.parametrize("budget", [0, 21, True, float("nan"), float("inf")])
def test_invalid_budget_never_opens_session(prepared, budget):
    with pytest.raises(ValueError):
        prepare(budget=budget)
    assert not prepared.paths and prepared.closed == 0


@pytest.mark.parametrize("field,value", [("camera_id", "other"), ("device_id", "654321")])
def test_cross_camera_enrollment_rejected_before_socket(prepared, field, value):
    with pytest.raises(ValueError):
        module.prepare_ptz_route(replace(ENTRY, **{field: value}), IDENTITY,
                                 camera_id="cam_test", direction="left")
    assert prepared.closed == 0 and not prepared.paths


@pytest.mark.parametrize("failure", ["identity", "axis", "target", "offline", "error", "bool_error"])
def test_failed_evidence_closes_socket_without_constructing_motion(prepared, failure):
    if failure == "identity":
        prepared.values[1]["swVer"] = "different"
    elif failure == "axis":
        prepared.values[2]["stVal"]["ptzInfo"]["id0_status"] = 5
    elif failure == "target":
        prepared.target = 654321
    elif failure == "offline":
        prepared.status = 0
    else:
        prepared.error = False if failure == "bool_error" else 7
    with pytest.raises(P2PProbeError):
        prepare()
    assert prepared.closed == 1 and not prepared.constructed
    if failure == "identity":
        assert len(prepared.paths) == 2


def test_expired_handshake_does_not_read_or_construct(prepared, monkeypatch):
    def expired(*args):
        prepared.now = 31
        return object(), SimpleNamespace(device_id=123456, status=1), 1
    monkeypatch.setattr(module, "open_camera_session", expired)
    with pytest.raises(P2PProbeError, match="time budget"):
        prepare()
    assert prepared.closed == 1 and not prepared.paths and not prepared.constructed


def test_constructor_failure_closes_socket(prepared, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("synthetic")
    monkeypatch.setattr(module, "NativePtzRoute", fail)
    with pytest.raises(OSError):
        prepare()
    assert prepared.closed == 1


def test_final_read_cannot_return_an_expired_route(prepared, monkeypatch):
    original = module.exchange_model_read
    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        if len(prepared.paths) == 3:
            prepared.now = 30
        return result
    monkeypatch.setattr(module, "exchange_model_read", slow)
    with pytest.raises(P2PProbeError, match="time budget"):
        prepare()
    assert prepared.closed == 1 and not prepared.constructed


def test_handshake_exception_closes_socket(prepared, monkeypatch):
    def fail(*args):
        raise P2PProbeError("synthetic handshake failure")
    monkeypatch.setattr(module, "open_camera_session", fail)
    with pytest.raises(P2PProbeError):
        prepare()
    assert prepared.closed == 1 and not prepared.paths
