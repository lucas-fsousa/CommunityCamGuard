"""Exclusive socket check: identity reads before/after one correlated heartbeat."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from .broker_account import AccountEvents, BrokerAccount
from .broker_heartbeat import HeartbeatCompletion
from .broker_maintenance import BrokerMaintenance
from .broker_read_socket import ReadSocket, ReadSpec
from .broker_reads import ReadCompletion
from .contracts import CertifiedNode


@dataclass(frozen=True, slots=True)
class AccountCheckResult:
    before: tuple[ReadCompletion, ...]
    after: tuple[ReadCompletion, ...]
    heartbeats: tuple[HeartbeatCompletion, ...]
    failure: str | None
    sent_datagrams: int
    received_datagrams: int


class _Stop(Exception):
    pass


def run_owned_account_check(sock: ReadSocket, node: CertifiedNode, generation: int,
                            specs: tuple[ReadSpec, ...], local_ip: str, local_port: int, *,
                            clock: Callable[[], float] = time.monotonic,
                            cancelled: Callable[[], bool] = lambda: False) -> AccountCheckResult:
    """Transfer socket ownership, <=60 seconds, <=3 reads per phase, no retry.

    Caller holds camera/account locks and verifies enrollment. No certification,
    discovery, media, actions or threads here. Returned values are internal only.
    """
    account = maintenance = None
    before: list[ReadCompletion] = []
    after: list[ReadCompletion] = []
    heartbeats: list[HeartbeatCompletion] = []
    phases: dict[int, bool] = {}
    sent = received = 0
    failure: str | None = None
    later = False

    def collect(events: AccountEvents) -> None:
        nonlocal failure
        for result in events.reads:
            (after if phases.get(result.request.ticket.sequence, False) else before).append(result)
        heartbeats.extend(events.heartbeats)
        if events.retired and events.retired != "closed":
            failure = failure or events.retired

    try:
        deadline = clock() + 60
        if not isinstance(specs, tuple) or not 1 <= len(specs) <= 3 or any(
                not isinstance(spec, ReadSpec) for spec in specs):
            raise ValueError("bounded account read specs required")
        account = BrokerAccount(node, generation, local_ip, local_port, clock=clock)
        maintenance = BrokerMaintenance.account_check(node, clock=clock)

        def check() -> None:
            nonlocal failure
            if cancelled():
                failure = "cancelled"
                raise _Stop
            if clock() >= deadline:
                failure = "deadline"
                raise _Stop

        def send(wire: bytes) -> None:
            nonlocal sent
            check()
            sock.settimeout(min(0.2, max(0.001, deadline - clock())))
            sent += 1
            if sock.sendto(wire, node.address) != len(wire):
                raise OSError("partial account datagram")

        def submit(second: bool) -> None:
            assert account is not None
            prepared = []
            for spec in specs:
                request = account.submit_read(spec.device_id, spec.path, spec.timeout)
                phases[request.request.ticket.sequence] = second
                prepared.append(request)
            for request in prepared:
                check()
                events = account.tick()
                collect(events)
                if events.reads or events.retired:
                    raise ValueError("read expired before complete batch submission")
                for heartbeat_wire in events.outgoing:
                    send(heartbeat_wire)
                send(request.wire)

        check()
        submit(False)
        while True:
            check()
            events = account.tick()
            collect(events)
            if failure:
                break
            for wire in events.outgoing:
                send(wire)
            if any(r.status != "response" or r.error_code != 0 for r in before + after):
                failure = "read_incomplete"
                break
            if heartbeats and not later:
                if len(before) != len(specs) or heartbeats[0].status != "response":
                    failure = "heartbeat_incomplete"
                    break
                later = True
                submit(True)
            if later and len(after) == len(specs):
                break
            sock.settimeout(min(0.05, max(0.001, deadline - clock())))
            try:
                wire, peer = sock.recvfrom(4096)
            except TimeoutError:
                continue
            received += 1
            check()
            complete: list[bytes] = []
            acknowledgements = maintenance.receive(wire, peer, inspect_message=complete.append)
            for plain in complete:
                collect(account.receive(plain, peer, generation=generation))
            for wire in acknowledgements:
                send(wire)
    except _Stop:
        pass
    except OSError:
        failure = failure or "socket"
    except ValueError:
        failure = failure or "protocol"
    finally:
        try:
            if account is not None:
                collect(account.retire())
            if maintenance is not None:
                maintenance.close()
        finally:
            try:
                sock.close()
            except OSError:
                failure = failure or "socket_close"
    return AccountCheckResult(tuple(before), tuple(after), tuple(heartbeats), failure, sent, received)
