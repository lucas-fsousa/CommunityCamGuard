"""Opt-in browser smoke test. Run inside a memory/CPU/time-limited cgroup.

Serves a short H.264 fixture or --settings/--recordings test assets on loopback.
No dashboard login, real camera connection or production API is involved.
Optional --width and --screenshot allow isolated component layout review.
"""

import argparse
import base64
import json
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_test_safety import require_bounded_browser, stop_browser
from websockets.sync.client import connect

COMPONENT_ASSETS = {
    "style.css": "style.css", "core.js": "modules/core.js", "i18n.js": "i18n.js",
    "recordings.js": "modules/recordings.js", "recording-playback.js": "modules/recording-playback.js",
    "settings.js": "modules/settings.js", "access-keys.js": "modules/access-keys.js",
    "access-key-dialog.js": "modules/access-key-dialog.js",
    "camera-controls.js": "modules/camera-controls.js", "control-actions.js": "modules/camera-control-actions.js",
    "audio-message.js": "modules/audio-message.js", "push-to-talk.js": "modules/push-to-talk.js",
    "session-access.js": "modules/session-access.js", "notifications.js": "modules/notifications.js",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", required=True, type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--settings", action="store_true", help="Check isolated settings layout instead")
    parser.add_argument("--recordings", action="store_true", help="Check isolated recordings loading overlay")
    parser.add_argument("--access-keys", action="store_true", help="Check delegated-access modal with synthetic data")
    parser.add_argument("--camera-controls", action="store_true", help="Check controls and toasts without camera traffic")
    parser.add_argument("--autoplay-block", action="store_true", help="Require a gesture for audible fixture playback")
    parser.add_argument("--seek-seconds", type=float, default=3, help="Seek position in the bounded fixture")
    parser.add_argument("--throttle-kib", type=int, default=0, help="Bound fixture response rate to test seeking before full download")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    fixture = args.fixture.resolve() if args.fixture else None
    component = args.settings or args.recordings or args.access_keys or args.camera_controls
    if sum((args.settings, args.recordings, args.access_keys, args.camera_controls)) > 1:
        parser.error("choose one component")
    if component and args.autoplay_block:
        parser.error("autoplay check requires a video fixture, not a component")
    if not 0 <= args.seek_seconds <= 1800:
        parser.error("seek position outside bounded test range")
    if not 0 <= args.throttle_kib <= 1024 or (component and args.throttle_kib):
        parser.error("throttling requires a video fixture and a rate from 0 to 1024 KiB/s")
    if not 280 <= args.width <= 2560 or not 320 <= args.height <= 1600:
        parser.error("viewport outside bounded test range")
    require_bounded_browser(args.browser)
    if not component and (not fixture or not fixture.is_file() or not 0 < fixture.stat().st_size <= 10 * 1024 * 1024):
        parser.error("fixture must be an existing H.264 MP4 below 10 MiB, at least 4 seconds")
    root = Path(__file__).resolve().parents[1]
    assets = {
        "/": (root / "tests/frontend/recording-browser.html", "text/html"),
        "/recording-playback.js": (root / "frontend/modules/recording-playback.js", "text/javascript"),
    }
    if component:
        page_name = "settings-browser.html" if args.settings else "recordings-overlay-browser.html"
        if args.access_keys:
            page_name = "access-keys-browser.html"
        if args.camera_controls:
            page_name = "camera-controls-browser.html"
        assets = {"/": (root / "tests/frontend" / page_name, "text/html")}
        for name, path in COMPONENT_ASSETS.items():
            assets["/" + name] = (root / "frontend" / path, "text/css" if name.endswith("css") else "text/javascript")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/api/recordings/file" and not component:
                if parse_qs(url.query).get("original") == ["true"]:
                    data, mime = b"deliberately invalid native fixture", "video/mp4"
                else:
                    data, mime = fixture.read_bytes(), "video/mp4"
            elif url.path in assets:
                file, mime = assets[url.path]
                data = file.read_bytes()
            else:
                self.send_error(404)
                return
            size = len(data)
            start, end = 0, size - 1
            ranged = self.headers.get("Range", "")
            if ranged:
                try:
                    first, last = ranged.removeprefix("bytes=").split("-", 1)
                    start = int(first) if first else max(0, size - int(last))
                    end = min(int(last), size - 1) if first and last else size - 1
                    if not 0 <= start <= end < size:
                        raise ValueError()
                except ValueError:
                    self.send_error(416)
                    return
            self.send_response(206 if ranged else 200)
            self.send_header("Content-Type", mime)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            if ranged:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                if args.throttle_kib and mime == "video/mp4":
                    for offset in range(start, end + 1, 8192):
                        chunk = data[offset:min(offset + 8192, end + 1)]
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        time.sleep(len(chunk) / (args.throttle_kib * 1024))
                else:
                    self.wfile.write(data[start:end + 1])
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="ccg-browser-") as profile:
            browser = subprocess.Popen([
                str(args.browser), "--headless", "--no-sandbox", "--disable-gpu",
                "--disable-dev-shm-usage", "--disable-background-networking",
                "--disable-component-update", "--disable-extensions", "--disable-sync",
                "--no-first-run", "--no-default-browser-check", "--mute-audio",
                "--renderer-process-limit=1", "--remote-debugging-port=0",
                f"--window-size={args.width},{args.height}",
                "--autoplay-policy=" + ("document-user-activation-required" if args.autoplay_block else "no-user-gesture-required"),
                f"--user-data-dir={profile}",
                f"http://127.0.0.1:{server.server_port}/?seek={args.seek_seconds}&throttled={int(bool(args.throttle_kib))}" + ("&autoplay=blocked" if args.autoplay_block else ""),
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                port_file = Path(profile) / "DevToolsActivePort"
                deadline = time.monotonic() + 45
                while not port_file.exists():
                    if browser.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("browser did not start")
                    time.sleep(0.1)
                port = int(port_file.read_text().splitlines()[0])
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as response:
                    pages = json.load(response)
                page = next(page for page in pages if page["type"] == "page")
                with connect(page["webSocketDebuggerUrl"], open_timeout=5) as socket:
                    if component:
                        socket.send(json.dumps({"id": 3, "method": "Emulation.setDeviceMetricsOverride",
                            "params": {"width": args.width, "height": args.height, "deviceScaleFactor": 1, "mobile": False}}))
                        socket.recv(timeout=5)
                        socket.send(json.dumps({"id": 4, "method": "Page.reload"}))
                        socket.recv(timeout=5)
                        time.sleep(0.3)
                    while time.monotonic() < deadline:
                        socket.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                            "expression": "window.smokeResult || (window.smokePhase === 'gesture' ? {gestureNeeded: true} : null)", "returnByValue": True,
                        }}))
                        result = json.loads(socket.recv(timeout=5))["result"]["result"].get("value")
                        if result and result.get("gestureNeeded") and args.autoplay_block:
                            socket.send(json.dumps({"id": 5, "method": "Runtime.evaluate", "params": {
                                "expression": "window.resumeRecording()", "userGesture": True,
                            }}))
                            socket.recv(timeout=5)
                            continue
                        if result:
                            print(json.dumps(result), flush=True)
                            if args.screenshot:
                                socket.send(json.dumps({"id": 2, "method": "Page.captureScreenshot"}))
                                capture = json.loads(socket.recv(timeout=5))
                                args.screenshot.write_bytes(base64.b64decode(capture["result"]["data"]))
                            if not result["ok"]:
                                raise SystemExit(1)
                            break
                        time.sleep(0.2)
                    else:
                        raise RuntimeError("browser test deadline exceeded")
            finally:
                stop_browser(browser)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
