"""Bounded responder for observed live AV MTP meter requests; no timer traffic."""

from dataclasses import dataclass

from .kcp_receive import ReceiveError
from .media_protocol import build_media_meter_ack, parse_media_meter


@dataclass
class AvMeter:
    peer: tuple[str, int]
    link_id: int
    call_id: int
    access_id: int
    device_id: int
    acknowledgements: int = 0

    def receive(self, wire: bytes, source: tuple[str, int]) -> bytes | None:
        if source != self.peer or len(wire) not in (74, 78):
            return None
        meter = parse_media_meter(wire)
        if (meter is None or meter.kind != 1 or meter.channel_type != 4
                or meter.record_length != len(wire) - 6
                or meter.link_id != self.link_id or meter.source_id != self.device_id
                or meter.destination_id != self.access_id
                or (meter.call_id is not None and meter.call_id != self.call_id)
                or meter.role not in (1, 2, 3)):
            return None
        if self.acknowledgements >= 64:
            raise ReceiveError("AV meter response budget exceeded")
        self.acknowledgements += 1
        return build_media_meter_ack(wire)
