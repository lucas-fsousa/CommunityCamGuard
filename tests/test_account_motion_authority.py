from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p.account_read_lifecycle import AccountReadLifecycle
from backend.app.drivers.yoosee.p2p.ptz_authority import PtzAuthority, PtzGrant
from tests.test_account_read_lifecycle import connection
from tests.test_account_worker_ptz import SPEC, MotionSocket

GRANT = PtzGrant(123, 456, frozenset({"right"}))


@pytest.mark.parametrize("grants", [[GRANT], (GRANT,) * 9, (GRANT, GRANT),
    (replace(GRANT, access_id=True),), (replace(GRANT, device_id=0),),
    (replace(GRANT, directions=frozenset({"diagonal"})),),
    (GRANT, PtzGrant(124, 457, frozenset({"right"})))])
def test_invalid_scope_is_rejected_before_auth(grants):
    with pytest.raises(ValueError): AccountReadLifecycle(motion_grants=grants)


@pytest.mark.parametrize("changes", [dict(access_id=124), dict(device_id=457),
    dict(direction="left"), dict(reviewed_directions=frozenset({"left", "right"}))])
def test_caller_cannot_expand_driver_scope(changes):
    authority = PtzAuthority((GRANT,))
    with pytest.raises(ValueError): authority.authorize(replace(SPEC, **changes))
    authority.authorize(SPEC)


def test_disabled_and_inactive_lifecycle_reject_motion():
    with pytest.raises(ValueError): AccountReadLifecycle().submit_motion(SPEC)
    with pytest.raises(ValueError):
        AccountReadLifecycle(motion_grants=(GRANT,)).submit_motion(SPEC)
    assert "123" not in repr(GRANT) and "456" not in repr(GRANT)


def test_authorized_gesture_uses_lifecycle_and_stale_ticket_cannot_cross_generation(monkeypatch):
    current = [MotionSocket()]
    owner = AccountReadLifecycle(clock=lambda: current[0].now, motion_grants=(GRANT,))
    tickets = []
    for _ in range(2):
        sock = current[0]
        receive = sock.recvfrom
        admitted = []
        def receiving(size, admitted=admitted, receive=receive):
            if not admitted:
                # Reject another device without occupying the one authorized slot.
                with pytest.raises(ValueError): owner.submit_motion(replace(SPEC, device_id=457))
                ticket = owner.submit_motion(SPEC)
                admitted.append(ticket)
                if tickets:
                    assert not owner.cancel_motion(tickets[0])
                    assert owner.take_motion(tickets[0]) is None
                tickets.append(ticket)
            return receive(size)
        monkeypatch.setattr(sock, "recvfrom", receiving)
        result = owner.run(lambda _, sock=sock: connection(sock))
        assert result.motion.release_delivery_confirmed and owner.state == "idle"
        assert owner.take_motion(tickets[-1]).release_delivery_confirmed
        assert len(sock.moves) == 2 and sock.closed == 1
        current[0] = MotionSocket()
    assert tickets[0].generation != tickets[1].generation


def test_drain_rejects_motion_before_worker_sees_it(monkeypatch):
    sock = MotionSocket()
    owner = AccountReadLifecycle(clock=lambda: sock.now, motion_grants=(GRANT,))
    receive = sock.recvfrom
    def receiving(size):
        owner.drain()
        with pytest.raises(ValueError): owner.submit_motion(SPEC)
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", receiving)
    owner.run(lambda _: connection(sock))
    assert not sock.moves and sock.closed == 1
