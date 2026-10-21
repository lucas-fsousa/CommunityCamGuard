"""Correlated, global IPv4 TCP relay selection; no socket or discovery."""

import ipaddress
import struct

from .crypto import gute_mode1_xor_checksum
from .mtp_relays import inspect_mtp_relay_advertisement


def select_relay(frame: bytes, session_id: int, link_id: int) -> tuple[str, int] | None:
    if len(frame) < 24 or gute_mode1_xor_checksum(frame) != struct.unpack_from("<I", frame, 16)[0]:
        return None
    advert = inspect_mtp_relay_advertisement(frame, expected_session_id=session_id,
                                           expected_link_id=link_id)
    if advert is None or any(entry.family != 4 for entry in advert.entries):
        return None
    for entry in advert.entries:
        if entry.flags & 4 and entry.tcp_port and ipaddress.ip_address(entry.address).is_global:
            return entry.address, entry.tcp_port
    return None
