"""Close timing and errors with synthetic channels; no camera or sockets."""

from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee.p2p.account_media_channel import finish_media_channels


def test_group_arms_and_pumps_all_tcp_closes_before_waiting_for_any_one():
    now, events = [0.0], []

    class Tcp:
        result = None
        until = None

        def begin_finish(self):
            self.until = now[0] + 2

        def step(self, *, budget_seconds):
            assert 0 < budget_seconds <= 0.2
            events.append('close_sent')

        def finish(self):
            assert events == ['close_sent'] * 3
            now[0] = max(now[0], self.until)
            self.result = 'closed'

    pumps = [Tcp() for _ in range(3)]
    channels = tuple(SimpleNamespace(tcp=pump, finish_media=pump.finish) for pump in pumps)
    finish_media_channels(channels)
    assert now[0] == 2  # The serial implementation spent six seconds.
    assert all(pump.result == 'closed' for pump in pumps)


def test_group_retires_siblings_before_propagating_an_uncertain_close():
    events = []

    def fail():
        events.append('uncertain')
        raise OSError('synthetic physical close failure')

    channels = (SimpleNamespace(tcp=None, finish_media=fail),
                SimpleNamespace(tcp=None, finish_media=lambda: events.append('closed')))
    with pytest.raises(OSError):
        finish_media_channels(channels)
    assert events == ['uncertain', 'closed']
