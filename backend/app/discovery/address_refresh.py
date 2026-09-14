"""Bounded, credential-free LAN identity lookup for DHCP recovery.

Never opens a media stream or sends stored credentials to a stale IP. MAC matching
is a LAN identity hint, not cryptographic authentication. Routed/NAT deployments
need a responding ONVIF identity service when ARP cannot identify the endpoint.
"""

from __future__ import annotations

import ipaddress
import threading
from concurrent.futures import ThreadPoolExecutor

from ..control import device
from . import active_scan, scan_lock

MAX_HOSTS = 512
IDENTITY_PORTS = (80, 8000, 8080, 8899, 5000)


def targets(subnets: list[str]) -> list[str]:
    if not subnets:
        local = active_scan._subnet_from_local()
        subnets = [local] if local else []
    networks = [ipaddress.ip_network(value, strict=False) for value in subnets]
    if any(net.version != 4 for net in networks):
        raise ValueError("DHCP recovery requires IPv4 subnets")
    if sum(net.num_addresses for net in networks) > MAX_HOSTS:
        raise ValueError("DHCP recovery subnet budget exceeded (512 addresses)")
    return list(dict.fromkeys(str(host) for net in networks for host in net.hosts()))


def find_addresses(
    macs: set[str], subnets: list[str], stop: threading.Event,
) -> dict[str, str]:
    if not scan_lock.acquire(blocking=False):
        return {}
    try:
        return _find_addresses(macs, subnets, stop)
    finally:
        scan_lock.release()


def _find_addresses(
    macs: set[str], subnets: list[str], stop: threading.Event,
) -> dict[str, str]:
    """Four workers, bounded targets, no login/path probing or camera writes.

    Duplicate identities are rejected rather than picking whichever replied last.
    """
    wanted = {mac.lower() for mac in macs if mac}
    if not wanted:
        return {}

    def identify(ip: str) -> tuple[str, set[str]]:
        identities: set[str] = set()
        # A TCP knock refreshes neighbor resolution even when RTSP is closed.
        if stop.is_set():
            return ip, identities
        rtsp_open = active_scan._port_open(ip, 554, 0.25)
        arp = active_scan._mac_for(ip)
        if rtsp_open and arp and arp.lower() in wanted:
            return ip, {arp.lower()}
        for port in IDENTITY_PORTS:
            if stop.is_set():
                break
            try:
                if active_scan._port_open(ip, port, 0.25):
                    mac = device.mac_address(ip, port=port, timeout=1.0)
                    if mac and mac.lower() in wanted:
                        identities.add(mac.lower())
                        break
            except (OSError, ValueError):
                continue
        return ip, identities

    matches: dict[str, set[str]] = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="dhcp-lookup") as pool:
        for ip, identities in pool.map(identify, targets(subnets)):
            for mac in identities:
                matches.setdefault(mac, set()).add(ip)
    return {mac: next(iter(ips)) for mac, ips in matches.items() if len(ips) == 1}
