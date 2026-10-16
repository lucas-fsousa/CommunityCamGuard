"""Opt-in real HTTPS dashboard logout/expiry/revocation in two browser tabs.

Creates one short-lived, zero-permission test key. No media or camera commands.
Run under the browser safety cgroup limits. Never logs cookies or credentials.
"""
import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from scripts.browser_cdp import bounded_browser
from scripts.check_delegated_access import check, connection_options

LOGIN = "!!document.querySelector('#login:not(.hidden)') && !!document.querySelector('#dash.hidden')"
DASH = "!!document.querySelector('#dash:not(.hidden)') && !!document.querySelector('#login.hidden')"


def wait_pages(browser, pages, expression, deadline):
    pending = set(pages)
    while pending and time.monotonic() < deadline:
        for page in tuple(pending):
            if browser.evaluate(page, expression):
                pending.remove(page)
        if pending:
            time.sleep(0.2)
    if pending:
        raise RuntimeError("Dashboard state deadline exceeded")


def exercise(client, owner, origin, browser, mode, width):
    key_id = None
    expires = datetime.now(UTC) + timedelta(seconds=45)
    expiry_at = time.monotonic() + (expires - datetime.now(UTC)).total_seconds()
    try:
        created = check(client, "POST", "/api/access-keys", 201, owner, {
            "label": "Browser session deployment check", "permissions": [],
            "expires_at": expires.isoformat(),
        }).json()
        key_id = created["metadata"]["id"]
        first, second = browser.page(width), browser.page(width)
        print(json.dumps({"stage": "browser_login"}), flush=True)
        browser.call("Page.navigate", {"url": origin}, first)
        wait_pages(browser, [first], LOGIN, time.monotonic() + 15)
        # Exercise the real form; the primary credential never enters Chromium.
        browser.evaluate(first, "document.querySelector('#login-key').value=" +
                         json.dumps(created["secret"]) +
                         "; document.querySelector('#login-form').requestSubmit(); true")
        wait_pages(browser, [first], DASH, time.monotonic() + 10)
        browser.call("Page.navigate", {"url": origin}, second)
        print(json.dumps({"stage": "second_tab"}), flush=True)
        wait_pages(browser, [second], DASH, time.monotonic() + 10)
        if time.monotonic() >= expiry_at:
            raise RuntimeError("Test session expired before acceptance setup")
        for page in (first, second):
            if not browser.evaluate(page, "document.querySelectorAll('video,video-stream').length === 0"):
                raise RuntimeError("Unexpected media element in zero-permission session")
        started = time.monotonic()
        print(json.dumps({"stage": "invalidate", "mode": mode}), flush=True)
        if mode == "revoke":
            check(client, "POST", f"/api/access-keys/{key_id}/revoke", token=owner, body={})
        elif mode == "logout":
            browser.evaluate(first, "document.querySelector('#btn-logout').click(); true")
        else:
            started = expiry_at
        wait_pages(browser, [first, second], LOGIN, started + 10)
        delay = round((time.monotonic() - started) * 1000)
        if delay < 0:
            raise RuntimeError("Early logout does not prove expiry")
        # Reload must not restore the expired/revoked/logged-out dashboard.
        for page in (first, second):
            browser.evaluate(page, "window.__ccgBeforeReload = true")
            browser.call("Page.reload", {}, page)
        wait_pages(browser, [first, second],
                   "!window.__ccgBeforeReload && document.readyState === 'complete' && (" + LOGIN + ")",
                   time.monotonic() + 10)
        for page in (first, second):
            anonymous = browser.evaluate(page, "fetch('/api/me', {cache:'no-store'})"
                                        ".then(r=>r.json()).then(me=>me.authenticated===false)")
            if not anonymous:
                raise RuntimeError("Reload retained an authenticated session")
        return dict(mode=mode, tabs_returned_to_login=2, invalidation_ms=delay,
                    reload_stays_logged_out=True, width=width, camera_permissions=0)
    finally:
        if key_id is not None:
            check(client, "POST", f"/api/access-keys/{key_id}/revoke", token=owner, body={})
        else:
            print(json.dumps({"key_creation_outcome_unknown": True,
                              "test_key_has_45_second_expiry": True}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", required=True, type=Path)
    parser.add_argument("--mode", choices=["revoke", "expiry", "logout"], required=True)
    parser.add_argument("--width", type=int, choices=[375, 1280], default=1280)
    args = parser.parse_args()
    from backend.app.config import get_settings
    settings = get_settings()
    options = connection_options(settings, public=True)
    with bounded_browser(args.browser) as browser:
        with httpx.Client(**options, timeout=10, trust_env=False, follow_redirects=False) as client:
            owner = check(client, "POST", "/api/login",
                          body={"key": settings.dashboard_secret_key}).cookies["ccg_session"]
            print(json.dumps(exercise(client, owner, options["base_url"], browser,
                                      args.mode, args.width)), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        safe = {"Dashboard state deadline exceeded", "Browser command failed",
                "Browser evaluation failed", "Browser startup failed",
                "Browser command exceeded budget", "Test session expired before acceptance setup",
                "Unexpected media element in zero-permission session",
                "Early logout does not prove expiry"}
        print(json.dumps({"check_failed": True, "error_type": type(exc).__name__,
                          "reason": str(exc) if str(exc) in safe else "check_failed"}), flush=True)
        sys.exit(1)
