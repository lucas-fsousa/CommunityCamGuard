"""Opt-in browser smoke test. Run inside a memory/CPU/time-limited cgroup.

Only serves an explicit short H.264 fixture and repository test assets on loopback.
No dashboard login, real camera connection or production API is involved.
"""

import argparse
import json
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from websockets.sync.client import connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    args = parser.parse_args()
    fixture = args.fixture.resolve()
    if not fixture.is_file() or not 0 < fixture.stat().st_size <= 10 * 1024 * 1024:
        parser.error("fixture must be an existing H.264 MP4 below 10 MiB, at least 4 seconds")
    root = Path(__file__).resolve().parents[1]
    assets = {
        "/": (root / "tests/frontend/recording-browser.html", "text/html"),
        "/recording-playback.js": (root / "frontend/modules/recording-playback.js", "text/javascript"),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/api/recordings/file":
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
            if ranged:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
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
                "--autoplay-policy=no-user-gesture-required", f"--user-data-dir={profile}",
                f"http://127.0.0.1:{server.server_port}/",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                    while time.monotonic() < deadline:
                        socket.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                            "expression": "window.smokeResult || null", "returnByValue": True,
                        }}))
                        result = json.loads(socket.recv(timeout=5))["result"]["result"].get("value")
                        if result:
                            print(json.dumps(result), flush=True)
                            if not result["ok"]:
                                raise SystemExit(1)
                            break
                        time.sleep(0.2)
                    else:
                        raise RuntimeError("browser test deadline exceeded")
            finally:
                browser.terminate()
                try:
                    browser.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait(timeout=5)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
