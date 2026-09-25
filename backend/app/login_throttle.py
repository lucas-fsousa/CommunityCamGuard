"""Fixed-memory, per-origin login pacing; no submitted credentials are retained.

Hashed buckets deliberately trade rare shared quotas for bounded state without an
attacker-driven eviction/reset loophole. This is not distributed DDoS protection.
"""

import hashlib
import ipaddress
import math
import secrets
import threading
import time

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse

_INITIALIZE = threading.Lock()

class LoginThrottle:
    BURST = 10
    INTERVAL = 6.0
    SLOTS = 4096

    def __init__(self):
        self._salt = secrets.token_bytes(16)
        self._next = [0.0] * self.SLOTS
        self._lock = threading.Lock()

    def _slot(self, host: str) -> int:
        try:
            address = ipaddress.ip_address(host)
            if isinstance(address, ipaddress.IPv6Address):
                address = address.ipv4_mapped or ipaddress.ip_network(f"{address}/64", strict=False).network_address
            identity = str(address)
        except ValueError:
            identity = "unknown"
        digest = hashlib.blake2s(identity.encode(), key=self._salt, digest_size=4).digest()
        return int.from_bytes(digest, "big") % self.SLOTS

    def retry_after(self, host: str) -> int:
        slot = self._slot(host)
        with self._lock:
            now = time.monotonic()
            earliest = self._next[slot] - (self.BURST - 1) * self.INTERVAL
            if earliest > now:
                return math.ceil(earliest - now)
            self._next[slot] = max(now, self._next[slot]) + self.INTERVAL
            return 0


class LoginRoute(APIRoute):
    """Reserve quota before FastAPI parses the login body, including invalid JSON."""

    def get_route_handler(self):
        handler = super().get_route_handler()
        if self.path != "/api/login" or "POST" not in (self.methods or set()):
            return handler
        async def limited(request: Request):
            # Some FastAPI versions rebuild this handler on each included-router
            # request. State belongs to the application, never the handler closure.
            with _INITIALIZE:
                throttle = getattr(request.app.state, "login_throttle", None)
                if throttle is None:
                    throttle = LoginThrottle()
                    request.app.state.login_throttle = throttle
            # Never consult X-Forwarded-For/Forwarded directly. The bundled server
            # disables ASGI-server proxy rewriting; custom launchers must do likewise.
            retry = throttle.retry_after(request.client.host if request.client else "unknown")
            if retry:
                return JSONResponse({"detail": "Too many login attempts"}, status_code=429,
                                    headers={"Retry-After": str(retry), "Cache-Control": "no-store"})
            response = await handler(request)
            response.headers["Cache-Control"] = "no-store"
            return response

        return limited
