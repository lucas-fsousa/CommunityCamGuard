import pytest

from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from backend.app.drivers.yoosee.p2p.tcp_pending_output import PendingTcpOutput


def test_unattempted_tail_is_sent_once_in_order_on_the_next_owner_turn():
    batch = PendingTcpOutput()
    batch.offer((b'a', b'b', b'c'))
    sent = []

    def send(wire):
        if len(sent) == 2: return False
        sent.append(wire)
        return True

    assert not batch.drain(send) and sent == [b'a', b'b']
    assert batch.drain(lambda wire: sent.append(wire) is None)
    assert sent == [b'a', b'b', b'c']
    assert batch.drain(lambda _: pytest.fail('completed batch cannot be replayed'))


def test_ambiguous_write_closes_the_batch_and_can_never_be_retried():
    batch = PendingTcpOutput()
    batch.offer((b'first', b'second'))
    attempted = []

    def uncertain(wire):
        attempted.append(wire)
        raise OSError('ambiguous write')

    with pytest.raises(OSError): batch.drain(uncertain)
    assert batch.closed and attempted == [b'first']
    with pytest.raises(ReceiveError): batch.drain(uncertain)
    assert attempted == [b'first']


@pytest.mark.parametrize('wires', [[], (b'',), ('private',), (bytes(32769),), (b'x',) * 257])
def test_batches_are_count_and_byte_bounded_and_fail_closed(wires):
    batch = PendingTcpOutput()
    with pytest.raises(ReceiveError) as error: batch.offer(wires)
    assert batch.closed and 'private' not in str(error.value)


def test_pending_batch_cannot_accumulate_additional_reads_or_controls():
    batch = PendingTcpOutput()
    batch.offer((bytes(32768),))
    with pytest.raises(ReceiveError): batch.offer((b'extra',))
    assert batch.closed
