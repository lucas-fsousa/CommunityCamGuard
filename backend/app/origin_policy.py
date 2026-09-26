"""Explicit browser-origin boundary, independent of forwarded-header identity."""

import ipaddress
import re
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection

from .config import get_settings


def normalize_origin(value: str) -> str:
    if (not value or len(value) > 2048 or not value.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or any(char in value for char in "?#\\%")):
        raise ValueError("Invalid dashboard origin")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {"", "/"} or parsed.netloc.endswith(":")):
        raise ValueError("Invalid dashboard origin")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{ipaddress.IPv6Address(host).compressed}]"
    elif not re.fullmatch(r"[a-z0-9.-]+", host):
        raise ValueError("Invalid dashboard origin")
    port = parsed.port
    if port == 0:
        raise ValueError("Invalid dashboard origin")
    suffix = f":{port}" if port is not None and port != (443 if parsed.scheme == "https" else 80) else ""
    return f"{parsed.scheme}://{host}{suffix}"


def target_origin(connection: HTTPConnection) -> str:
    hosts = connection.headers.getlist("host")
    if len(hosts) != 1 or "/" in hosts[0]:
        raise ValueError("Invalid host")
    configured = get_settings().dashboard_public_origin
    scheme = (urlsplit(configured).scheme if configured else
              "https" if connection.url.scheme in {"https", "wss"} else "http")
    actual = normalize_origin(f"{scheme}://{hosts[0]}")
    if configured and actual != configured:
        raise ValueError("Host does not match configured origin")
    return configured or actual


def browser_origin_allowed(connection: HTTPConnection) -> bool:
    try:
        expected = target_origin(connection)
        origins = connection.headers.getlist("origin")
        sites = connection.headers.getlist("sec-fetch-site")
        if len(origins) > 1 or len(sites) > 1 or (sites and sites[0].lower() not in {"same-origin", "none"}):
            return False
        if origins:
            return normalize_origin(origins[0]) == expected
        referers = connection.headers.getlist("referer")
        if len(referers) > 1:
            return False
        if referers:
            parsed = urlsplit(referers[0])
            return normalize_origin(f"{parsed.scheme}://{parsed.netloc}") == expected
        # Non-browser scripts need no Origin. Reject simple HTML form submissions
        # without one, rather than relying on SameSite cookies alone.
        media = connection.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        return media not in {"application/x-www-form-urlencoded", "multipart/form-data", "text/plain"}
    except ValueError:
        return False


def require_target_origin(connection: HTTPConnection) -> None:
    try:
        target_origin(connection)
    except ValueError as exc:
        raise HTTPException(403, "Dashboard origin denied", headers={"Cache-Control": "no-store"}) from exc


def require_browser_write(request: Request) -> None:
    require_target_origin(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not browser_origin_allowed(request):
        raise HTTPException(403, "Cross-origin request denied", headers={"Cache-Control": "no-store"})


def secure_cookie(request: Request) -> bool:
    target = target_origin(request)
    return request.url.scheme == "https" or target.startswith("https://")
