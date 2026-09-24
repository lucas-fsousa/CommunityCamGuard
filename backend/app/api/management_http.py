"""Shared browser-write boundary for primary-session management endpoints."""

from fastapi import HTTPException, Request


def require_json_same_origin(request: Request) -> None:
    """Reject cross-origin/form writes; absent Origin supports authenticated scripts.

    No forwarded headers are read here. Deployment must configure proxy trust.
    Origin has no path, even when the application is mounted below a root_path.
    """
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}".lower()
    if ((origin is not None and origin.lower() != expected)
            or request.headers.get("sec-fetch-site", "").lower() == "cross-site"):
        raise HTTPException(403, "Cross-origin management update denied")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "JSON required")
