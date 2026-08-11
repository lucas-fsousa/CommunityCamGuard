"""Automatic, conflict-free application build identity and frontend bootstrap wiring."""
from __future__ import annotations

import json
from pathlib import Path

from backend.app import main
from backend.app.frontend_build import build_version


def test_build_version_is_deterministic_and_changes_with_runtime_code(tmp_path: Path):
    root = tmp_path / "project"
    backend = root / "backend" / "app"
    frontend = root / "frontend"
    backend.mkdir(parents=True)
    frontend.mkdir()
    (backend / "main.py").write_text("VALUE = 1\n")
    (frontend / "app.js").write_text("console.log(1);\n")
    (frontend / "ignored.txt").write_text("not executable\n")

    first = build_version(root, frontend)
    assert first == build_version(root, frontend)
    assert first.startswith("b-") and len(first) == 14

    (frontend / "app.js").write_text("console.log(2);\n")
    assert build_version(root, frontend) != first


def test_bootstrap_has_no_manually_incremented_asset_versions():
    frontend = Path(__file__).parents[1] / "frontend"
    index = (frontend / "index.html").read_text()
    player = (frontend / "player.js").read_text()
    boot = (frontend / "boot.js").read_text()

    assert '<script src="/boot.js"></script>' in index
    assert "2026-" not in index
    assert "2026-" not in player
    assert 'fetch("/api/build"' in boot
    assert "__CCG_BUILD__" in boot


def test_build_endpoint_is_public_no_store_and_returns_content_id():
    response = main.frontend_build_info()
    body = json.loads(response.body)
    assert body["version"].startswith("b-")
    assert response.headers["cache-control"] == "no-store"
