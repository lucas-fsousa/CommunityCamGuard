import pytest

from backend.app.drivers.yoosee.p2p import av_route_io
from backend.app.drivers.yoosee.p2p.av_route_io import BudgetSocket
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError


class Socket:
    def settimeout(self, value):
        assert value > 0
    def sendto(self, wire, peer):
        return len(wire)
    def recvfrom(self, size):
        assert size <= 4096
        return b"noise", ("192.0.2.1", 1)


def test_absolute_deadline_and_separate_cleanup_budget(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(av_route_io.time, "monotonic", lambda: now[0])
    sock = BudgetSocket(Socket(), lambda: False)
    now[0] = 20
    with pytest.raises(P2PProbeError):
        sock.recvfrom(4096)
    sock.phase(1, cleanup=True)
    assert sock.sendto(b"cleanup", ("192.0.2.1", 1)) == 7
    now[0] = 21
    with pytest.raises(P2PProbeError):
        sock.check()


def test_cancellation_does_not_prevent_bounded_release():
    sock = BudgetSocket(Socket(), lambda: True)
    with pytest.raises(P2PProbeError):
        sock.check()
    sock.phase(1, cleanup=True)
    assert sock.check() > 0


@pytest.mark.parametrize("budget", ["packets", "received", "sent"])
def test_budgets_apply_to_all_io(budget):
    sock = BudgetSocket(Socket(), lambda: False)
    if budget == "packets":
        sock.packets = 10_000
    elif budget == "received":
        sock.received = 8 * 1024 * 1024
    else:
        sock.sent = 2 * 1024 * 1024
    with pytest.raises(P2PProbeError, match="budget"):
        if budget == "sent":
            sock.sendto(b"x", ("192.0.2.1", 1))
        else:
            sock.recvfrom(4096)
