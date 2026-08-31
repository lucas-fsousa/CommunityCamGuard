"""Server-side guard for operations that must never be exposed outside the trusted LAN.

Provisioning receives Wi-Fi credentials and can reconfigure nearby hardware.  Authentication is
not enough for that surface: an authenticated dashboard may deliberately be published through a
reverse proxy. Forwarding headers are treated as evidence, never as authority: any public hop
rejects the request. Direct clients must use a literal loopback/RFC1918/ULA address and same-origin
browser requests.
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, WebSocket
from starlette.requests import HTTPConnection

from ..config import get_settings

_TRUSTED_V4 = tuple(
    ipaddress.ip_network(value) for value in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_TRUSTED_V6 = tuple(ipaddress.ip_network(value) for value in ("::1/128", "fc00::/7", "fe80::/10"))
_LAN_ONLY_DETAIL = "provisioning is available only from the authenticated local network"


def _loopback_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _trusted_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    networks = _TRUSTED_V4 if isinstance(address, ipaddress.IPv4Address) else _TRUSTED_V6
    return any(address in network for network in networks)


def _local_hostname(value: str) -> bool:
    """Accept localhost or a literal trusted-LAN address; reject DNS rebinding names."""
    value = value.rstrip(".").lower()
    return value == "localhost" or _trusted_ip(value)


def _header_hostname(value: str) -> str:
    """Extract a hostname from Host, Origin, Referer or Forwarded ``for=`` values."""
    raw = value.strip().strip('"')
    if not raw or raw.lower() in {"null", "unknown"} or raw.startswith("_"):
        return ""
    # urlsplit needs ``//`` to interpret a bare host:port as an authority.
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    return parsed.hostname or ""


def _forwarded_addresses(request: HTTPConnection) -> list[str]:
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


def _require_local_connection(request: HTTPConnection) -> None:
    client = request.client
    host = _header_hostname(request.headers.get("host", ""))
    if client is None or not _trusted_ip(client.host) or not _local_hostname(host):
        raise HTTPException(status_code=403, detail=_LAN_ONLY_DETAIL)
    # A remote LAN peer claiming Host: localhost is not a localhost request. A loopback peer may
    # legitimately be an on-host HTTPS proxy addressing the app through its private LAN IP.
    if not _loopback_ip(client.host) and (host == "localhost" or _loopback_ip(host)):
        raise HTTPException(status_code=403, detail=_LAN_ONLY_DETAIL)

    for name in ("origin", "referer"):
        value = request.headers.get(name)
        if value:
            source_host = _header_hostname(value)
            if not _local_hostname(source_host) or source_host.rstrip(".").lower() != host.rstrip(".").lower():
                raise HTTPException(status_code=403, detail=_LAN_ONLY_DETAIL)

    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        raise HTTPException(status_code=403, detail=_LAN_ONLY_DETAIL)

    for value in _forwarded_addresses(request):
        if not _trusted_ip(_header_hostname(value)):
            raise HTTPException(status_code=403, detail=_LAN_ONLY_DETAIL)


def require_local_request(request: Request) -> None:
    """Reject HTTP requests not provably made from the authenticated local network."""

    _require_local_connection(request)


def require_local_websocket(websocket: WebSocket) -> None:
    """Apply the same trusted-client/host/origin rules before accepting a WebSocket."""

    _require_local_connection(websocket)


def require_local_or_remote_ble_request(request: Request) -> None:
    """Allow trusted-LAN provisioning or the BLE subset through an opted-in HTTPS tunnel."""
    try:
        require_local_request(request)
        return
    except HTTPException as local_error:
        if not get_settings().provisioning_remote_ble_enabled:
            raise local_error

    host = _header_hostname(request.headers.get("host", ""))
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    parsed_source = urlsplit(source)
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower()
    fetch_site = request.headers.get("sec-fetch-site", "").lower()
    if (
        not host
        or parsed_source.scheme.lower() != "https"
        or (parsed_source.hostname or "").lower() != host.lower()
        or forwarded_proto != "https"
        or fetch_site not in {"", "same-origin", "none"}
    ):
        raise HTTPException(
            status_code=403,
            detail="remote BLE provisioning requires a same-origin HTTPS tunnel",
        )
