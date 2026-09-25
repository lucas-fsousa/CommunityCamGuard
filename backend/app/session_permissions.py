"""Staged temporary-session permissions; unknown operations deny by default.

This is NOT the final guest product role. Login stays disabled until ordinary
operations, transport invalidation and UI cleanup can be enabled together.
Use the router's matched template, never substring/prefix matching of user URLs.
"""

_TEMPORARY_READS = frozenset({
    "/api/cameras", "/api/cameras/status", "/api/recordings",
    "/api/recordings/playback-status", "/api/storage",
    "/api/recordings/file", "/api/recordings/download",
    "/api/media/streams", "/api/media/activity",
})


def temporary_http_allowed(method: str, route_template: str | None) -> bool:
    return ((method == "GET" and route_template in _TEMPORARY_READS)
            or (method == "POST" and route_template == "/api/recordings/prepare"))
