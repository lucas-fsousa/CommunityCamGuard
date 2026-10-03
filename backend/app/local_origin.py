"""Narrow direct-LAN exception to a configured public dashboard origin.

This is not authentication or a replacement for provisioning's local-only guard.
Literal addresses avoid resolving attacker-controlled names into trusted ranges.
"""

import ipaddress

from starlette.requests import HTTPConnection

_V4 = tuple(ipaddress.ip_network(value) for value in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))
_V6 = tuple(ipaddress.ip_network(value) for value in ("::1/128", "fc00::/7", "fe80::/10"))
_FORWARDED = (
    "forwarded", "x-forwarded-for", "x-real-ip", "cf-connecting-ip",
    "true-client-ip", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port",
)


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        address = address.ipv4_mapped or address
    networks = _V4 if isinstance(address, ipaddress.IPv4Address) else _V6
    return address if any(address in network for network in networks) else None


def direct_local_origin_allowed(connection: HTTPConnection, hostname: str) -> bool:
    if connection.client is None or any(name in connection.headers for name in _FORWARDED):
        return False
    peer = _address(connection.client.host)
    target = _address("127.0.0.1" if hostname == "localhost" else hostname)
    if peer is None or target is None:
        return False
    # Remote LAN clients must not masquerade as a loopback request.
    return not target.is_loopback or peer.is_loopback
