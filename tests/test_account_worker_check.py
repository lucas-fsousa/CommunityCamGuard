import pytest

from backend.app.drivers.yoosee.p2p.account_worker_check import run_owned_worker_check
from backend.app.drivers.yoosee.p2p.broker_read_socket import ReadSpec
from backend.app.drivers.yoosee.p2p.contracts import ACCOUNT_IDENTITY_READ_PATHS
from tests.test_account_read_worker import WorkerSocket
from tests.test_broker_reads import DEVICE, NODE

SPECS = tuple(ReadSpec(DEVICE.device_id, path) for path in ACCOUNT_IDENTITY_READ_PATHS)


def run(sock, specs=SPECS, **kwargs):
    return run_owned_worker_check(sock, NODE, 1, specs, "192.0.2.1", 1234,
                                  clock=lambda: sock.now, **kwargs)


def test_real_worker_identity_check_has_explicit_success_criteria():
    sock = WorkerSocket()
    result = run(sock)
    assert result.failure is None and sock.closed == 1
    assert len(result.completions) == 3
    assert result.worker.heartbeat_responses == 1 and result.worker.reason == "deadline"
    assert sock.reads == 3 and sock.beats == 1
    assert "private" not in repr(result)


@pytest.mark.parametrize("fault,failure", [("send", "socket"), ("receive", "socket"),
    ("ack", "socket"), ("close", "socket_close"), ("read_silent", "read_incomplete"),
    ("heartbeat_silent", "heartbeat_timeout")])
def test_failure_is_not_hidden_by_some_successful_reads(fault, failure):
    sock = WorkerSocket(fault)
    result = run(sock)
    assert result.failure == failure and sock.closed == 1


@pytest.mark.parametrize("specs", [(), SPECS[:2], SPECS * 2,
    (ReadSpec(1, "Action.expelCtrl"), *SPECS[1:]),
    (ReadSpec(1, ACCOUNT_IDENTITY_READ_PATHS[0]), *SPECS[1:]),
    (ReadSpec(DEVICE.device_id, ACCOUNT_IDENTITY_READ_PATHS[0], -1), *SPECS[1:])])
def test_invalid_batch_closes_without_sending(specs):
    sock = WorkerSocket()
    with pytest.raises(ValueError): run(sock, specs)
    assert sock.closed == 1 and not sock.reads and not sock.beats


def test_cancellation_reaches_worker_before_send():
    sock = WorkerSocket()
    result = run(sock, cancelled=lambda: True)
    assert result.failure == "stopped" and sock.closed == 1 and not sock.reads


def test_interruption_still_closes_once():
    sock = WorkerSocket("interrupt")
    with pytest.raises(KeyboardInterrupt): run(sock)
    assert sock.closed == 1
