"""Shared browser-write boundary for primary-session management endpoints."""

from fastapi import HTTPException, Request

from ..origin_policy import require_browser_write


def require_json_same_origin(request: Request) -> None:
    """Reject cross-origin/form writes; absent Origin supports authenticated scripts.

    No forwarded headers are read here. Proxy deployments can pin the public origin.
    Origin has no path, even when the application is mounted below a root_path.
    """
    require_browser_write(request)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "JSON required")
