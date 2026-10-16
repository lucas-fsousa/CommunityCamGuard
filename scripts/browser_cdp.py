"""Small bounded Chromium/CDP lifecycle for opt-in dashboard acceptance."""
import json
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from websockets.sync.client import connect

from scripts.browser_test_safety import require_bounded_browser, stop_browser


class Browser:
    def __init__(self, socket):
        self.socket = socket
        self.sequence = 0

    def call(self, method, params=None, session=None):
        self.sequence += 1
        request = dict(id=self.sequence, method=method, params=params or {})
        if session:
            request["sessionId"] = session
        self.socket.send(json.dumps(request))
        deadline = time.monotonic() + 10
        for _ in range(1024):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            response = json.loads(self.socket.recv(timeout=remaining))
            if response.get("id") != self.sequence:
                continue
            if "error" in response:
                raise RuntimeError("Browser command failed")
            return response.get("result", {})
        raise RuntimeError("Browser command exceeded budget")

    def page(self, width):
        target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.call("Page.enable", {}, session)
        self.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": 800, "deviceScaleFactor": 1, "mobile": False,
        }, session)
        return session

    def evaluate(self, session, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        }, session)
        if "exceptionDetails" in result:
            raise RuntimeError("Browser evaluation failed")
        return result["result"].get("value")


@contextmanager
def bounded_browser(binary: Path):
    require_bounded_browser(binary)
    with tempfile.TemporaryDirectory(prefix="ccg-session-browser-") as profile:
        process = subprocess.Popen([
            str(binary), "--headless", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--disable-background-networking",
            "--disable-component-update", "--disable-extensions", "--disable-sync",
            "--no-first-run", "--no-default-browser-check", "--mute-audio",
            "--renderer-process-limit=1", "--remote-debugging-port=0",
            f"--user-data-dir={profile}", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            port_file = Path(profile) / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while not port_file.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Browser startup failed")
                time.sleep(0.1)
            port, endpoint = port_file.read_text().splitlines()[:2]
            with connect(f"ws://127.0.0.1:{int(port)}{endpoint}", open_timeout=5,
                         close_timeout=1, max_size=1024 * 1024, max_queue=4) as socket:
                yield Browser(socket)
        finally:
            stop_browser(process)
