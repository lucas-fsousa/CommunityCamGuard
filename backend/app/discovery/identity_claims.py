"""Batch-local identity ambiguity checks; no probing or registry mutation."""

from .active_scan import ScannedHost


def unambiguous_identities(hosts: list[ScannedHost]) -> set[str]:
    """Accept repeated observations, not competing addresses or alias targets.

    ARP and ONVIF may legitimately name different interfaces of one device. An
    alias is usable only when every observation associates it with the same
    address and authoritative identity. This is a LAN hint, not authentication.
    """
    claims: dict[str, set[tuple[str, str]]] = {}
    addresses: dict[str, set[str]] = {}
    for host in hosts:
        if not host.mac:
            continue
        primary = host.mac.lower()
        addresses.setdefault(host.address, set()).add(primary)
        for identity in (primary, host.arp_mac.lower() if host.arp_mac else ""):
            if identity:
                claims.setdefault(identity, set()).add((host.address, primary))
    return {identity for identity, observations in claims.items()
            if len(observations) == 1
            and all(len(addresses[address]) == 1 for address, _ in observations)}
