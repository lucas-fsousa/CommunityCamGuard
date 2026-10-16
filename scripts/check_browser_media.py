"""Explicit camera-only HTTPS rendered-video expiry/revocation acceptance.

Test-only page instrumentation filters the real camera list and rejects every
other stream/control write. It does not change server grants or media payloads.
"""
import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from scripts.browser_cdp import bounded_browser
from scripts.check_browser_session import DASH, LOGIN, wait_pages
from scripts.check_delegated_access import check, connection_options


def isolation_source(camera_id):
    if not re.fullmatch(r"cam_[0-9a-f]{24}", camera_id):
        raise ValueError("Invalid camera id")
    return Path(__file__).with_name("browser_media_probe.js").read_text().replace(
        "__CAMERA_ID__", json.dumps(camera_id))


def exercise(client, owner, origin, browser, camera_id, mode):
    source = isolation_source(camera_id)
    key_id = None
    expires = datetime.now(UTC) + timedelta(seconds=50)
    expiry_at = time.monotonic() + (expires - datetime.now(UTC)).total_seconds()
    try:
        created = check(client, "POST", "/api/access-keys", 201, owner, {
            "label": "Rendered media deployment check", "permissions": ["live"],
            "expires_at": expires.isoformat(),
        }).json()
        key_id = created["metadata"]["id"]
        page = browser.page(1280)
        browser.call("Page.addScriptToEvaluateOnNewDocument", {"source": source}, page)
        print(json.dumps({"stage": "login"}), flush=True)
        browser.call("Page.navigate", {"url": origin}, page)
        wait_pages(browser, [page], LOGIN, time.monotonic() + 15)
        if not browser.evaluate(page, "typeof window.__ccgMediaProbe?.observe === 'function'"):
            raise RuntimeError("Media isolation not installed")
        browser.evaluate(page, "document.querySelector('#login-key').value=" +
                         json.dumps(created["secret"]) +
                         "; document.querySelector('#login-form').requestSubmit(); true")
        wait_pages(browser, [page], DASH, time.monotonic() + 10)
        print(json.dumps({"stage": "rendered_frames"}), flush=True)
        print(json.dumps({"probe_state": browser.evaluate(page,
              "({installed:!!window.__ccgMediaProbe, "
              "observer:typeof window.__ccgMediaProbe?.observe, "
              "blocked:window.__ccgMediaProbe?.blocked})")}), flush=True)
        wait_pages(browser, [page], "!!document.querySelector('cam-player')?.video",
                   time.monotonic() + 10)
        print(json.dumps({"stage": "observe_video"}), flush=True)
        if not browser.evaluate(page, "window.__ccgMediaProbe.observe()"):
            raise RuntimeError("Frame observation unavailable")
        wait_pages(browser, [page], "window.__ccgMediaProbe.frames >= 8 && "
                   "window.__ccgMediaProbe.lastTime > window.__ccgMediaProbe.firstTime",
                   min(time.monotonic() + 20, expiry_at))
        if time.monotonic() >= expiry_at:
            raise RuntimeError("No rendered frames before expiry")
        print(json.dumps({"stage": "invalidate", "mode": mode}), flush=True)
        started = time.monotonic()
        if mode == "revoke":
            check(client, "POST", f"/api/access-keys/{key_id}/revoke", token=owner, body={})
        else:
            started = expiry_at
        wait_pages(browser, [page], LOGIN + " && window.__ccgMediaProbe.stopped()", started + 10)
        delay = round((time.monotonic() - started) * 1000)
        if delay < 0:
            raise RuntimeError("Early stop does not prove expiry")
        sockets = browser.evaluate(page, "window.__ccgMediaProbe.sockets.length")
        # Let pending reconnect timers surface; never run another live session.
        time.sleep(2)
        stable = browser.evaluate(page, "window.__ccgMediaProbe.stopped() && "
                                  "window.__ccgMediaProbe.blocked === 0 && "
                                  f"window.__ccgMediaProbe.sockets.length === {sockets}")
        if not stable:
            raise RuntimeError("Media cleanup was not stable")
        return dict(mode=mode, presented_frames=8, login_and_media_stopped_ms=delay,
                    reconnect_during_grace=False, sockets=sockets, camera_control_writes=0)
    finally:
        if key_id is not None:
            check(client, "POST", f"/api/access-keys/{key_id}/revoke", token=owner, body={})
        else:
            print(json.dumps({"key_creation_outcome_unknown": True,
                              "test_key_has_50_second_expiry": True}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--mode", choices=["expiry", "revoke"], required=True)
    parser.add_argument("--loopback", action="store_true", help="Diagnostic without public proxy")
    args = parser.parse_args()
    isolation_source(args.camera_id)
    from backend.app.config import get_settings
    settings = get_settings()
    options = connection_options(settings, public=not args.loopback)
    with bounded_browser(args.browser) as browser:
        with httpx.Client(**options, timeout=10, trust_env=False, follow_redirects=False) as client:
            owner = check(client, "POST", "/api/login",
                          body={"key": settings.dashboard_secret_key}).cookies["ccg_session"]
            print(json.dumps(exercise(client, owner, options["base_url"], browser,
                                      args.camera_id, args.mode)), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        safe = {"Dashboard state deadline exceeded", "Frame observation unavailable",
                "No rendered frames before expiry", "Early stop does not prove expiry",
                "Media cleanup was not stable", "Browser evaluation failed",
                "Media isolation not installed"}
        print(json.dumps({"check_failed": True, "error_type": type(exc).__name__,
                          "reason": str(exc) if str(exc) in safe else "check_failed"}), flush=True)
        sys.exit(1)
