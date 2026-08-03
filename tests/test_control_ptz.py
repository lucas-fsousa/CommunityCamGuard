"""Tests for ONVIF PTZ control (backend/app/control/ptz.py).

The network layer (`_send_soap_nowait` / `_post_soap`) is stubbed so these are fast and offline;
we assert the control logic (direction mapping, IP guard, status handling).
"""
import pytest

from backend.app.control import ptz
from backend.app.db.registry import Camera


def _cam(ip="10.0.0.10"):
    return Camera(mac="aa:bb:cc:00:00:10", last_ip=ip)


# --- velocity_for -------------------------------------------------------------------

def test_velocity_for_known_directions():
    assert ptz.velocity_for("left") == (-1.0, 0.0)
    assert ptz.velocity_for("right") == (1.0, 0.0)
    assert ptz.velocity_for("up") == (0.0, 1.0)
    assert ptz.velocity_for("down") == (0.0, -1.0)


def test_velocity_for_is_case_and_whitespace_insensitive():
    assert ptz.velocity_for("  LEFT ") == (-1.0, 0.0)


@pytest.mark.parametrize("bad", [None, "", "diagonal", "zoom", "north"])
def test_velocity_for_unknown_raises(bad):
    with pytest.raises(ValueError):
        ptz.velocity_for(bad)


# --- start / halt / move (fire-and-forget nowait path) ------------------------------

def test_start_fires_a_move_and_reports_success(monkeypatch):
    sent = []
    monkeypatch.setattr(ptz, "_send_soap_nowait",
                        lambda ip, body, **k: sent.append((ip, body)) or True)
    assert ptz.start(_cam(), "left") is True
    assert sent and sent[0][0] == "10.0.0.10"
    assert "ContinuousMove" in sent[0][1]


def test_start_returns_false_when_the_camera_send_fails(monkeypatch):
    monkeypatch.setattr(ptz, "_send_soap_nowait", lambda *a, **k: False)
    assert ptz.start(_cam(), "left") is False


def test_start_without_ip_is_false_and_sends_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(ptz, "_send_soap_nowait", lambda *a, **k: called.append(1) or True)
    assert ptz.start(_cam(ip=""), "left") is False
    assert called == []


def test_start_unknown_direction_raises_before_touching_the_network(monkeypatch):
    monkeypatch.setattr(ptz, "_send_soap_nowait",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    with pytest.raises(ValueError):
        ptz.start(_cam(), "sideways")


def test_halt_sends_stop_and_reports_true(monkeypatch):
    sent = []
    monkeypatch.setattr(ptz, "_send_soap_nowait",
                        lambda ip, body, **k: sent.append(body) or True)
    assert ptz.halt(_cam()) is True
    assert sent and "Stop" in sent[0]


def test_halt_without_ip_is_false(monkeypatch):
    monkeypatch.setattr(ptz, "_send_soap_nowait",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    assert ptz.halt(_cam(ip="")) is False


def test_move_pulses_then_always_stops(monkeypatch):
    sent = []
    monkeypatch.setattr(ptz, "_send_soap_nowait",
                        lambda ip, body, **k: sent.append(body) or True)
    assert ptz.move(_cam(), "right", duration=0) is True
    # a move followed by a stop
    assert any("ContinuousMove" in b for b in sent)
    assert any("Stop" in b for b in sent)


def test_move_without_ip_is_false():
    assert ptz.move(_cam(ip=""), "right", duration=0) is False


# --- supports_ptz (blocking probe path) ---------------------------------------------

def test_supports_ptz_true_on_200(monkeypatch):
    monkeypatch.setattr(ptz, "_post_soap", lambda *a, **k: 200)
    monkeypatch.setattr(ptz, "_send_soap_nowait", lambda *a, **k: True)   # the follow-up Stop
    assert ptz.supports_ptz("10.0.0.10") is True


def test_supports_ptz_false_when_not_200(monkeypatch):
    monkeypatch.setattr(ptz, "_post_soap", lambda *a, **k: None)
    assert ptz.supports_ptz("10.0.0.10") is False
