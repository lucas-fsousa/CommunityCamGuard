"""Finite LAN route measurements on the account's existing UDP reader/socket."""

import logging
import struct
import time
from collections.abc import Callable

from .bootstrap_evidence import classify_meter
from .media_protocol import build_media_meter_request, parse_media_meter
from .media_route_job import MediaRouteJob

log = logging.getLogger(__name__)


class _Measurement:
    def __init__(self, route: MediaRouteJob, now: float):
        self.peer = route.lan_endpoint
        self.started = now
        self.sent: set[tuple[int, int]] = set()
        self.done = False


class AccountLanProbe:
    """Two meters per admitted media route, no AV INIT or route/config change.

    Only the owning account calls poll/receive. There is no login, thread,
    recvfrom, socket ownership or fallback decision here. A missing/failed LAN
    candidate cannot stop TCP, delay its startup, or quarantine a sibling.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._probes: dict[MediaRouteJob, _Measurement] = {}
        self._disabled: set[int] = set()

    def disable(self, device_id: int) -> None:
        self._disabled.add(device_id)

    def poll(self, routes: tuple[MediaRouteJob, ...]) -> tuple[tuple[bytes, tuple[str, int]], ...]:
        active = {route for route in routes if route.accepts_direct_probe}
        for route in tuple(self._probes):
            if route not in active:
                del self._probes[route]
        packets = []
        now = self._clock()
        for route in routes:
            if route not in active or route.endpoint is None:
                continue
            if route.device_id in self._disabled:
                route.lan_probe_complete = True
                route.lan_roundtrip_confirmed = False
                continue
            probe = self._probes.get(route)
            if probe is None:
                probe = self._probes[route] = _Measurement(route, now)
                if probe.peer is None:
                    probe.done = True
                    route.lan_probe_complete = True
                    log.info("native LAN measurement outcome=no_candidate")
            if probe.done:
                continue
            if now - probe.started >= 1.5:
                probe.done = True
                route.lan_probe_complete = True
                log.info("native LAN measurement outcome=no_roundtrip attempts=%d", len(probe.sent))
                continue
            count = len(probe.sent)
            if count >= 2 or (count and now - probe.started < 0.25):
                continue
            assert probe.peer is not None
            wire = build_media_meter_request(route.access_id, route.device_id,
                route.attempt.link_id, route.attempt.call_id, sequence=count + 1,
                timestamp=int(now * 1000) & 0xFFFFFFFF)
            meter = parse_media_meter(wire)
            assert meter is not None
            probe.sent.add((meter.sequence, meter.timestamp))
            packets.append((wire, probe.peer))
        return tuple(packets)

    def receive(self, wire: bytes, peer: tuple[str, int]) -> None:
        # A fixed-size meter, never media buffers or arbitrary application data.
        if len(wire) not in (74, 78):
            return
        meter = parse_media_meter(wire)
        if meter is None or meter.kind != 2:
            return
        for route, probe in self._probes.items():
            if (probe.done or not route.accepts_direct_probe or peer != probe.peer
                    or self._clock() - probe.started >= 1.5
                    or meter.link_id != route.attempt.link_id
                    or meter.source_id != route.device_id or meter.destination_id != route.access_id):
                continue
            if classify_meter(meter, wire, route.attempt.call_id, probe.sent) != {"reply"}:
                continue
            # classify_meter checks the full echoed timestamp and exact sent pair.
            assert struct.unpack_from("<Q", wire, 38)[0] == meter.timestamp
            probe.done = True
            route.lan_roundtrip_confirmed = True
            route.lan_probe_complete = True
            log.info("native LAN measurement outcome=roundtrip attempts=%d", len(probe.sent))

    def close(self) -> None:
        self._probes.clear()
        self._disabled.clear()
