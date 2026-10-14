"""Experimental finite account composition; single caller, no sockets or worker."""

import math
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .account_output_budget import AccountOutputBudget
from .broker_heartbeat import BrokerHeartbeat, HeartbeatCompletion
from .broker_reads import BrokerReads, ReadCompletion, ReadRequest
from .broker_sequences import BrokerSequences
from .broker_transactions import ReceiptTicket
from .contracts import CertifiedNode
from .model_protocol import build_model_read
from .ptz_scheduler import PtzScheduler

Retirement = Literal["closed", "transport", "lifetime", "heartbeat_timeout",
                     "input_budget", "drained", "drain_timeout", "prepare"]


@dataclass(frozen=True, slots=True)
class AccountRead:
    request: ReadRequest
    wire: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class AccountEvents:
    reads: tuple[ReadCompletion, ...] = ()
    heartbeats: tuple[HeartbeatCompletion, ...] = ()
    outgoing: tuple[bytes, ...] = field(default=(), repr=False)
    retired: Retirement | None = None


class BrokerAccount:
    """120-second research owner: eight reads plus one reserved heartbeat slot.

    Accepts complete, already-decrypted frames from the sole socket reader. That
    reader still owns cryptographic validation, fragment assembly and response
    ACKs. No production PTZ/cache integration or automatic recertification.
    """

    def __init__(self, node: CertifiedNode, generation: int, local_ip: str, local_port: int, *,
                 clock: Callable[[], float] = time.monotonic):
        self._source = BrokerSequences(node.session_id, generation, node.next_sequence)
        self._reads = BrokerReads(node.session_id, generation, node.next_sequence,
                                  sequences=self._source, clock=clock)
        self._heartbeat = BrokerHeartbeat(node.session_id, generation, self._source, clock=clock)
        self._node: CertifiedNode | None = node
        self._generation, self._clock = generation, clock
        self._local = local_ip, local_port
        now = clock()
        self._deadline, self._next_heartbeat = now + 120, now + 50
        self._drain_deadline: float | None = None
        self._reason: Retirement | None = None
        self._messages = self._bytes = 0
        self._ptz: PtzScheduler | None = None

    @property
    def motion_cleanup_pending(self) -> bool:
        """Retirement is not permission to close a socket with an unfinished job."""
        return self._ptz is not None and self._ptz.result is None

    def prepare_ptz(self, output: AccountOutputBudget, *, access_id: int, device_id: int,
                    direction: str, reviewed_directions: frozenset[str],
                    duration: float = 0.2) -> PtzScheduler:
        """Trusted sole-owner API, not mailbox admission or a capability grant.

        Caller authorizes enrollment/model and drives the returned job on this
        account's existing socket through terminal STOP, even after retirement.
        Uses the SAME sequence source as reads/heartbeats; never a second login.
        """
        if (self.state != "active" or self._deadline - self._clock() < 4
                or self.motion_cleanup_pending):
            raise ValueError("account not accepting PTZ")
        if (self._ptz is not None and self._ptz.result is not None
                and self._ptz.result.start_attempted
                and not self._ptz.result.release_delivery_confirmed):
            self.retire("transport")
            raise ValueError("previous PTZ release unconfirmed")
        assert self._node is not None
        job = PtzScheduler(self._node, self._source, output, generation=self._generation,
            access_id=access_id, device_id=device_id, direction=direction,
            reviewed_directions=reviewed_directions, duration=duration, clock=self._clock)
        self._ptz = job
        return job

    @property
    def state(self) -> Literal["active", "draining", "retired"]:
        return "retired" if self._reason else "draining" if self._drain_deadline is not None else "active"

    def submit_read(self, device_id: int, path: str, timeout: float = 2.0) -> AccountRead:
        if (self.state != "active" or self._clock() >= self._deadline
                or (self._ptz is not None and self._ptz.result is not None
                    and self._ptz.result.start_attempted
                    and not self._ptz.result.release_delivery_confirmed)):
            raise ValueError("account not accepting reads; poll for terminal events")
        if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                or timeout > self._deadline - self._clock()):
            raise ValueError("read exceeds account lifetime")
        request = self._reads.register(device_id, path, timeout)
        assert self._node is not None
        try:
            wire = build_model_read(self._node, device_id, path, request.ticket.sequence, secrets.randbits(31))
        except BaseException:
            self._reads.cancel(request.ticket)
            raise
        return AccountRead(request, wire)

    def retire(self, reason: Retirement = "closed") -> AccountEvents:
        if reason not in ("closed", "transport", "lifetime", "heartbeat_timeout", "input_budget",
                          "drained", "drain_timeout", "prepare"):
            raise ValueError("unknown account retirement reason")
        if self._reason is not None:
            return AccountEvents(retired=self._reason)
        if self._ptz is not None:
            self._ptz.cancel()
        self._reason = reason
        self._source.close()  # Block late deliveries before releasing consumer state.
        reads = self._reads.close()
        heartbeat = self._heartbeat.close()
        self._node = None
        return AccountEvents(reads, (heartbeat,) if heartbeat else (), retired=reason)

    def cancel_read(self, ticket: ReceiptTicket) -> ReadCompletion | None:
        """Release one exact reservation; never replay a wire request."""
        return self._reads.cancel(ticket)

    def _expire(self) -> AccountEvents:
        if self._reason is not None:
            return AccountEvents(retired=self._reason)
        if (self._ptz is not None and self._ptz.result is not None
                and self._ptz.result.start_attempted
                and not self._ptz.result.release_delivery_confirmed):
            return self.retire("transport")
        if self._clock() >= self._deadline:
            return self.retire("lifetime")
        reads = self._reads.poll()
        heartbeat = self._heartbeat.poll()
        if heartbeat is not None:
            tail = self.retire("heartbeat_timeout")
            return AccountEvents(reads + tail.reads, (heartbeat,), retired=tail.retired)
        if self._drain_deadline is not None:
            empty = (not self._reads.pending_count and not self._heartbeat.pending_count
                     and not self.motion_cleanup_pending)
            if empty or self._clock() >= self._drain_deadline:
                tail = self.retire("drained" if empty else "drain_timeout")
                return AccountEvents(reads + tail.reads, tail.heartbeats, retired=tail.retired)
        return AccountEvents(reads)

    def tick(self) -> AccountEvents:
        events = self._expire()
        if (self.state != "active" or self._heartbeat.pending_count
                or self._clock() < self._next_heartbeat):
            return events
        assert self._node is not None
        try:
            prepared = self._heartbeat.prepare(self._node, *self._local)
        except Exception:
            tail = self.retire("prepare")
            return AccountEvents(events.reads + tail.reads, tail.heartbeats, retired=tail.retired)
        return AccountEvents(events.reads, outgoing=(prepared.wire,))

    def receive(self, plain: bytes, peer: tuple[str, int], *, generation: int) -> AccountEvents:
        events = self._expire()
        if self._node is None:
            return events
        if not isinstance(plain, bytes):
            tail = self.retire("input_budget")
            return AccountEvents(events.reads + tail.reads, tail.heartbeats, retired=tail.retired)
        self._messages += 1
        self._bytes += len(plain)
        if self._messages > 512 or self._bytes > 1024 * 1024 or len(plain) > 4096:
            tail = self.retire("input_budget")
            return AccountEvents(events.reads + tail.reads, tail.heartbeats, retired=tail.retired)
        if peer != self._node.address or type(generation) is not int or generation != self._generation:
            return events
        read = self._reads.accept(plain, generation=generation)
        heartbeat = self._heartbeat.accept(plain, generation=generation)
        if heartbeat is not None:
            self._next_heartbeat = self._clock() + self._heartbeat.cadence_seconds
        return AccountEvents(events.reads + ((read,) if read else ()),
                             (heartbeat,) if heartbeat else ())

    def drain(self) -> AccountEvents:
        """Stop admission/keepalives; finish outstanding work for at most two seconds."""
        if self.state == "active":
            self._drain_deadline = self._clock() + 2
            if self._ptz is not None:
                self._ptz.cancel()
        return self._expire()
