"""Passive broker-provided LAN candidate; no discovery, socket or route selection."""

import ipaddress
import struct

from .crypto import gute_mode1_xor_checksum
from .mtp_relays import inspect_mtp_relay_advertisement


def parse_mtp_lan_endpoint(frame: bytes, session_id: int, link_id: int) -> tuple[str, int] | None:
    """Read the authenticated A3 peerlanip/host-order port, not a relay record.

    Split SDK510bc51a, gat_on_rcvpkt_MTP_RES_RESPONSE0x247ad0–0x247b28:
    IPv4 at0x64, host-order port at0x5a, added as LAN/NAT kind2. The
    outer endpoint's port at0x58 is NETWORK-order instead. Caller still must
    authenticate/decrypt the broker packet; checksum/correlation are not auth.
    Returning a candidate does not prove reachability or authorize contacting it.
    """
    if inspect_mtp_relay_advertisement(frame, expected_session_id=session_id,
                                     expected_link_id=link_id) is None:
        return None
    if gute_mode1_xor_checksum(frame) != struct.unpack_from("<I", frame, 16)[0]:
        return None
    address = ipaddress.IPv4Address(frame[0x64:0x68])
    # This is deliberately the LAN candidate only. Do not reinterpret localhost,
    # multicast, reserved/public broker data as a camera on the private LAN.
    if not any(address in network for network in _PRIVATE_NETWORKS):
        return None
    if frame[0x64:0x68] == frame[0x60:0x64]:
        return None  # SDK does not add the duplicate outer address as LAN.
    port = struct.unpack_from("<H", frame, 0x5A)[0]
    return (str(address), port) if port else None


_PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))
