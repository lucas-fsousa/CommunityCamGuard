"""Server-side guard for operations that must never be exposed off-host.

Provisioning receives Wi-Fi credentials and can reconfigure nearby hardware.  Authentication is
not enough for that surface: an authenticated dashboard may deliberately be published through a
reverse proxy.  The checks here require a real loopback connection *and* a loopback HTTP origin.
Forwarding headers are treated as evidence, never as authority: any non-loopback hop rejects the
request.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException, Request


def _loopback_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _local_hostname(value: str) -> bool:
    """Accept only literal loopback addresses or the exact DNS name ``localhost``."""
    value = value.rstrip(".").lower()
    return value == "localhost" or _loopback_ip(value)


def _header_hostname(value: str) -> str:
    """Extract a hostname from Host, Origin, Referer or Forwarded ``for=`` values."""
    raw = value.strip().strip('"')
    if not raw or raw.lower() in {"null", "unknown"} or raw.startswith("_"):
        return ""
    # urlsplit needs ``//`` to interpret a bare host:port as an authority.
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    return parsed.hostname or ""


def _forwarded_addresses(request: Request) -> list[str]:
    values: list[str] = []
    for name in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip", "true-client-ip"):
        for raw in request.headers.getlist(name):
            values.extend(part.strip() for part in raw.split(",") if part.strip())
    for raw in request.headers.getlist("forwarded"):
        for element in raw.split(","):
            for parameter in element.split(";"):
                key, separator, value = parameter.strip().partition("=")
                if separator and key.lower() == "for":
                    values.append(value.strip())
    return values


def require_local_request(request: Request) -> None:
    """FastAPI dependency that rejects every request not provably made via localhost.

    The generic 403 deliberately does not disclose which check failed.  This guard must remain on
    every provisioning route even when the normal dashboard session dependency is also present.
    """
    client = request.client
    host = _header_hostname(request.headers.get("host", ""))
    if client is None or not _loopback_ip(client.host) or not _local_hostname(host):
        raise HTTPException(status_code=403, detail="provisioning is available only on localhost")

    for name in ("origin", "referer"):
        value = request.headers.get(name)
        if value and not _local_hostname(_header_hostname(value)):
            raise HTTPException(status_code=403, detail="provisioning is available only on localhost")

    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(status_code=403, detail="provisioning is available only on localhost")

    for value in _forwarded_addresses(request):
        if not _loopback_ip(_header_hostname(value)):
            raise HTTPException(status_code=403, detail="provisioning is available only on localhost")
