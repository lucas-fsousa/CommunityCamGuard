"""Operator-only deployment check. Run inside the app container via stdin.

Default: health/build and aggregate camera status only. --exercise-access-keys
creates two clearly labeled credentials, exercises login/denials/expiry and revokes
both in finally. No camera control, stream, recording conversion or deletion.
Never print credentials, response bodies, device identities or exception details.
--public uses only the configured HTTPS dashboard origin, with certificate
verification and no redirects. The default remains a loopback check.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx


def connection_options(settings, public=False):
    origin = settings.dashboard_public_origin or f"http://127.0.0.1:{settings.port}"
    if public:
        from backend.app.origin_policy import normalize_origin
        origin = normalize_origin(settings.dashboard_public_origin)
        if urlsplit(origin).scheme != "https":
            raise ValueError("Public checks require the configured HTTPS origin")
        return {"base_url": origin, "headers": {"Origin": origin}}
    return {"base_url": f"http://127.0.0.1:{settings.port}",
            "headers": {"Host": urlsplit(origin).netloc, "Origin": origin}}


def check(client, method, path, expected=200, token=None, body=None):
    headers = {"Cookie": "ccg_session=" + token} if token else {}
    response = client.request(method, path, headers=headers, json=body)
    if response.status_code != expected:
        print(json.dumps({"http_failure": {"method": method,
            "route": "/".join(path.split("/")[:3]), "status": response.status_code,
            "expected": expected}}), flush=True)
        raise RuntimeError("Unexpected HTTP status")
    return response


def public_boundaries(client):
    """Run before login, with an empty cookie jar; no credential or file dump."""
    for route in ("/api/cameras", "/api/recordings", "/api/access-keys"):
        check(client, "GET", route, 401)
    for route in ("/.env", "/.git/config", "/data/ccg.db", "/temp/", "/re/"):
        check(client, "GET", route, 404)


def exercise(client, owner):
    keys = []
    creation_pending = False
    try:
        for expiring in (False, True):
            expires = (datetime.now(UTC) + timedelta(seconds=5)).isoformat() if expiring else None
            creation_pending = True
            created = check(client, "POST", "/api/access-keys", 201, owner, {
                "label": "Deployment check — " + ("expiry" if expiring else "revocation"),
                "expires_at": expires, "permissions": ["ptz"],
            }).json()
            keys.append(created["metadata"]["id"])
            creation_pending = False
            assert created["login_enabled"] is True
            login = check(client, "POST", "/api/login", body={"key": created["secret"]})
            guest = login.cookies["ccg_session"]
            me = check(client, "GET", "/api/me", token=guest).json()
            assert me["authentication"] == "temporary" and not me["can_manage"]
            assert me["permissions"] == ["ptz"]
            for route in ("/api/access-keys", "/api/settings", "/api/media/streams", "/api/recordings"):
                check(client, "GET", route, 403, guest)
            if expiring:
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if not check(client, "GET", "/api/me", token=guest).json()["authenticated"]:
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError("Expiration not enforced")
            else:
                check(client, "POST", "/api/access-keys/" + keys[-1] + "/revoke", token=owner, body={})
            assert not check(client, "GET", "/api/me", token=guest).json()["authenticated"]
            check(client, "POST", "/api/login", 401, body={"key": created["secret"]})
        return {"delegated_login": True, "permission_denials": True, "expiry": True, "revocation": True}
    finally:
        if creation_pending:
            # A lost response can hide a committed key. Do not retry creation or
            # revoke by label: another operator's check may use the same label.
            print(json.dumps({"test_key_creation_outcome_unknown": True,
                              "review_deployment_check_keys": True}))
        failures = 0
        for key_id in keys:
            try:
                check(client, "POST", "/api/access-keys/" + key_id + "/revoke", token=owner, body={})
            except Exception:
                failures += 1
        if failures:
            print(json.dumps({"test_key_revocation_failed": failures}))
            raise RuntimeError("Test-key revocation needs operator attention")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise-access-keys", action="store_true")
    parser.add_argument("--public", action="store_true",
                        help="Check the configured HTTPS origin instead of loopback")
    args = parser.parse_args()
    from backend.app.config import get_settings
    settings = get_settings()
    with httpx.Client(**connection_options(settings, args.public), timeout=10,
                      trust_env=False, follow_redirects=False, verify=True) as client:
        assert check(client, "GET", "/health").json()["status"] == "ok"
        if args.public:
            public_boundaries(client)
        login = check(client, "POST", "/api/login", body={"key": settings.dashboard_secret_key})
        if args.public:
            cookie = login.headers.get("set-cookie", "").lower()
            assert "; secure" in cookie and "; httponly" in cookie and "samesite=lax" in cookie
        owner = login.cookies["ccg_session"]
        assert check(client, "GET", "/api/me", token=owner).json()["can_manage"]
        rejected = client.post("/api/logout", headers={"Cookie": "ccg_session=" + owner,
                               "Origin": "https://untrusted.invalid"}, json={})
        assert rejected.status_code == 403
        statuses = check(client, "GET", "/api/cameras/status", token=owner).json()
        result = {"transport": "public_https" if args.public else "loopback",
                  "cross_origin_denied": True,
                  "build": check(client, "GET", "/api/build").json()["version"],
                  "cameras": len(statuses), "online": sum(s.get("online") is True for s in statuses),
                  "recording": sum(s.get("recording") is True for s in statuses)}
        if args.exercise_access_keys:
            result.update(exercise(client, owner))
        if args.public:
            result["anonymous_and_sensitive_paths_denied"] = True
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"check_failed": True, "error_type": type(error).__name__}))
        sys.exit(1)
