"""Delegated-session permissions; unknown operations deny by default.

Each key stores explicit grants; none can grant server administration.
Use the router's matched template, never substring/prefix matching of user URLs.
"""

from .access_policy import LEGACY_PERMISSIONS


def temporary_http_allowed(method: str, route_template: str | None,
                           permissions: tuple[str, ...] = LEGACY_PERMISSIONS,
                           control_key: str | None = None) -> bool:
    grants = set(permissions)
    if method == "GET":
        if route_template in {"/api/cameras", "/api/cameras/status"}:
            return bool(grants)
        if route_template in {"/api/recordings", "/api/recordings/playback-status", "/api/storage",
                              "/api/recordings/file", "/api/recordings/download"}:
            return "recordings" in grants
        if route_template in {"/api/media/streams", "/api/media/activity"}:
            return "live" in grants
    if method == "POST":
        required = {
            "/api/recordings/prepare": "recordings",
            "/api/cameras/{camera_id}/ptz": "ptz",
            "/api/cameras/{camera_id}/reboot": "reboot",
            "/api/cameras/{camera_id}/intercom/messages": "intercom",
        }.get(route_template or "")
        return required is not None and required in grants
    if method in {"GET", "PUT"} and route_template in {
        "/api/cameras/{camera_id}/controls/{control_key}",
        "/api/cameras/{camera_id}/controls/{control_key}/options",
    }:
        return control_key in grants and control_key not in {"live", "recordings", "ptz", "intercom", "reboot"}
    return False
