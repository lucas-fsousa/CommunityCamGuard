"""Explicit HTTPS/WSS acceptance; creates/revokes test keys, never sends media.

Run with python -m scripts.check_public_channels --camera-id <registered id>.
Idle accepted sockets receive no MSE request: the app never opens go2rtc upstream.
No camera command, audio, playback or encoder. Revoked audit rows remain.
"""
import argparse
import inspect
import json
import re
import socket as network_socket
import sys
import time
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta

import httpx
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from scripts.check_delegated_access import check, connection_options


def socket_options(origin, token):
    options = dict(origin=origin, additional_headers={"Cookie": "ccg_session=" + token},
                   open_timeout=5, close_timeout=1, max_size=4096, max_queue=1,
                   compression=None)
    # Proxy auto-discovery was added after websockets12 (our runtime minimum).
    if "proxy" in inspect.signature(connect).parameters:
        options["proxy"] = None
    return options


def expect_denied(url, options, connector=connect):
    try:
        with connector(url, **options):
            raise RuntimeError("Unauthorized channel accepted")
    except InvalidStatus as exc:
        if exc.response.status_code != 403:
            print(json.dumps({"unexpected_handshake_status": exc.response.status_code}), flush=True)
            raise RuntimeError("Unexpected channel denial status") from None


def expect_invalidated(socket):
    started = time.monotonic()
    try:
        socket.recv(timeout=7)
    except ConnectionClosed as exc:
        if exc.rcvd is None or exc.rcvd.code != 1008:
            print(json.dumps({"unexpected_close_code": exc.rcvd.code if exc.rcvd else None}), flush=True)
            raise RuntimeError("Unexpected channel close") from None
        return round((time.monotonic() - started) * 1000)
    raise RuntimeError("Unexpected payload on idle channel")


def exercise(client, owner, origin, camera_id, connector=connect):
    keys = []
    pending = False

    def create(permissions, expires=None):
        nonlocal pending
        pending = True
        created = check(client, "POST", "/api/access-keys", 201, owner, {
            "label": "Channel deployment check", "permissions": permissions,
            "expires_at": expires,
        }).json()
        keys.append(created["metadata"]["id"])
        pending = False
        login = check(client, "POST", "/api/login", body={"key": created["secret"]})
        return login.cookies["ccg_session"]

    base = ("wss" + origin[5:]) if origin.startswith("https:") else ("ws" + origin[4:])
    url = base + "/api/go2rtc/ws?src=" + camera_id + "_hd"
    try:
        print(json.dumps({"stage": "permission_denials"}), flush=True)
        denied = create(["ptz"])
        expect_denied(url, socket_options(origin, denied), connector)
        expect_denied(base + f"/api/cameras/{camera_id}/intercom/stream",
                      socket_options(origin, denied), connector)
        print(json.dumps({"stage": "revocation"}), flush=True)
        live = create(["live"])
        revoked_id = keys[-1]
        other = create(["live"])
        with ExitStack() as sockets:
            print(json.dumps({"stage": "open_revocation_channel"}), flush=True)
            victim = sockets.enter_context(connector(url, **socket_options(origin, live)))
            print(json.dumps({"stage": "open_independent_channel"}), flush=True)
            unaffected = sockets.enter_context(connector(url, **socket_options(origin, other)))
            print(json.dumps({"stage": "revoke_open_channel"}), flush=True)
            check(client, "POST", f"/api/access-keys/{revoked_id}/revoke", token=owner, body={})
            revoke_ms = expect_invalidated(victim)
            if not unaffected.ping().wait(2):
                raise RuntimeError("Independent channel lost")
            try:
                unaffected.recv(timeout=0.1)
            except TimeoutError:
                pass
            else:
                raise RuntimeError("Unexpected independent channel payload")
        expect_denied(url, socket_options(origin, live), connector)
        print(json.dumps({"stage": "expiry"}), flush=True)
        expires = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
        expiring = create(["live"], expires)
        with connector(url, **socket_options(origin, expiring)) as victim:
            expiry_ms = expect_invalidated(victim)
        expect_denied(url, socket_options(origin, expiring), connector)
        return dict(ungranted_channels_denied=True, revoked_channel_closed_ms=revoke_ms,
                    expired_channel_closed_ms=expiry_ms, independent_channel_preserved=True,
                    media_requests_sent=0)
    finally:
        if pending:
            print(json.dumps({"test_key_creation_outcome_unknown": True}), flush=True)
        failures = 0
        for key in keys:
            try:
                check(client, "POST", f"/api/access-keys/{key}/revoke", token=owner, body={})
            except Exception:
                failures += 1
        if failures:
            print(json.dumps({"test_key_revocation_failed": failures}), flush=True)
            raise RuntimeError("Test key cleanup incomplete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--loopback", action="store_true", help="Compare direct local channel behavior")
    parser.add_argument("--proxy-loopback-port", type=int,
                        help="Diagnostic: connect to loopback nginx while preserving public Host/Origin")
    args = parser.parse_args()
    if not re.fullmatch(r"cam_[0-9a-f]{24}", args.camera_id):
        raise ValueError("Invalid camera id")
    from backend.app.config import get_settings
    settings = get_settings()
    if args.loopback and args.proxy_loopback_port:
        raise ValueError("Choose one diagnostic transport")
    if args.proxy_loopback_port is not None and not 1 <= args.proxy_loopback_port <= 65535:
        raise ValueError("Invalid local proxy port")
    options = connection_options(settings, public=not (args.loopback or args.proxy_loopback_port))
    origin = options["base_url"]
    connector = connect
    if args.proxy_loopback_port:
        origin = connection_options(settings, public=True)["base_url"]
        def local_proxy(url, **kwargs):
            sock = network_socket.create_connection(("127.0.0.1", args.proxy_loopback_port), timeout=5)
            try:
                return connect("ws" + url[3:], sock=sock, **kwargs)
            except BaseException:
                sock.close()
                raise
        connector = local_proxy
    with httpx.Client(**options, timeout=10, trust_env=False, follow_redirects=False) as client:
        login = check(client, "POST", "/api/login", body={"key": settings.dashboard_secret_key})
        owner = login.cookies["ccg_session"]
        cameras = check(client, "GET", "/api/cameras/status", token=owner).json()
        if not any(camera["id"] == args.camera_id for camera in cameras):
            raise ValueError("Camera is not registered")
        print(json.dumps(exercise(client, owner, origin, args.camera_id, connector)), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"check_failed": True, "error_type": type(exc).__name__}), flush=True)
        sys.exit(1)
