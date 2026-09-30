"""Keep opt-in browser fixtures aligned with transitive production imports."""

import importlib.util
import json
import re
from pathlib import Path

import pytest


@pytest.mark.parametrize("page", ["settings-browser.html", "access-keys-browser.html", "recordings-overlay-browser.html",
                                  "camera-controls-browser.html", "recordings-view-browser.html"])
def test_component_import_graph_resolves_to_served_assets(page, monkeypatch):
    root = Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    spec = importlib.util.spec_from_file_location("browser_fixture_runner", root / "scripts/check_recording_browser.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    source = (root / "tests/frontend" / page).read_text()
    mapping = json.loads(re.search(r'<script type="importmap">(.*?)</script>', source, re.S)[1])["imports"]
    pending, visited = [source], set()
    while pending:
        text = pending.pop()
        for imported in re.findall(r'\bfrom\s+[\'"]([^\'"]+)[\'"]', text):
            url = imported if imported.startswith("/") else mapping[imported]
            if url in visited:
                continue
            visited.add(url)
            asset = runner.COMPONENT_ASSETS[url.removeprefix("/")]
            pending.append((root / "frontend" / asset).read_text())
    assert visited
