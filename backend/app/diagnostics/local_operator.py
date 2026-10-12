"""Shared direct-loopback boundary for internal operator diagnostics."""

import ipaddress

from fastapi import HTTPException, Request

from ..api.local_only import require_local_request
from ..auth import require_auth


def require_local_operator(request: Request) -> None:
    require_auth(request)
    require_local_request(request)
    try:
        peer = ipaddress.ip_address(request.client.host if request.client else "")
    except ValueError:
        raise HTTPException(403, "diagnostic requires direct loopback") from None
    if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped:
        peer = peer.ipv4_mapped
    forwarded = ("forwarded", "x-forwarded-for", "x-real-ip", "cf-connecting-ip",
                 "true-client-ip", "x-forwarded-host", "x-forwarded-proto")
    if not peer.is_loopback or any(name in request.headers for name in forwarded):
        raise HTTPException(403, "diagnostic requires direct loopback")
