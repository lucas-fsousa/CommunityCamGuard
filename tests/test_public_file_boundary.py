"""Public asset/archive boundaries use only synthetic local files."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.api import recordings
from backend.app.config import get_settings
from backend.app.frontend_static import PUBLIC_ASSETS, DashboardFiles


@pytest.fixture
def public(tmp_path):
    root = tmp_path / "public"
    for name in PUBLIC_ASSETS:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic public asset")
    app = FastAPI(); app.mount("/", DashboardFiles(root))
    with TestClient(app) as client:
        yield root, client


def test_current_dashboard_assets_are_explicitly_reviewed():
    root = Path(__file__).parents[1] / "frontend"
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file() and
              path.suffix in {".html", ".js", ".css"} and "dist" not in path.relative_to(root).parts}
    assert actual == PUBLIC_ASSETS, "review new public assets before serving them"


def test_public_assets_and_conditional_requests_remain_usable(public):
    _, client = public
    for name in ["", *sorted(PUBLIC_ASSETS)]:
        response = client.get("/" + name)
        assert response.status_code == 200 and response.text == "synthetic public asset"
        assert response.headers["x-content-type-options"] == "nosniff"
    response = client.get("/app.js")
    assert client.get("/app.js", headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    assert client.head("/index.html").status_code == 200
    assert client.get("/app.js", headers={"Range": "bytes=0-8"}).status_code == 206


@pytest.mark.parametrize("name", [
    ".env", ".env.local", ".git/config", "data/ccg.db", "recordings/example.mp4",
    "re/session.log", "temp/session.json", "app.js.bak", "app.js.map", "backup.zip",
    "modules/private.js", "unexpected.html", "404.html", "credentials.pem",
])
def test_unlisted_files_are_denied_even_if_physically_inside_frontend(public, name):
    root, client = public
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("SYNTHETIC_SECRET_MARKER")
    for method in ("GET", "HEAD"):
        response = client.request(method, "/" + name)
        assert response.status_code == 404 and "SYNTHETIC_SECRET_MARKER" not in response.text


@pytest.mark.parametrize("url", ["/%2eenv", "/%2e%2e/.env", "/modules/%2e%2e/.env", "/.git%2fconfig", "/modules/", "/api/unknown"])
def test_encoded_paths_and_directory_fallback_do_not_expose_files(public, url):
    root, client = public
    (root / ".env").write_text("SYNTHETIC_SECRET_MARKER")
    response = client.get(url)
    assert response.status_code == 404 and "SYNTHETIC_SECRET_MARKER" not in response.text


@pytest.mark.parametrize("outside", [False, True])
def test_allowlisted_filename_cannot_alias_a_secret(public, tmp_path, outside):
    root, client = public
    secret = (tmp_path if outside else root) / "secret.txt"
    secret.write_text("SYNTHETIC_SECRET_MARKER")
    (root / "app.js").unlink()
    (root / "app.js").symlink_to(secret)
    assert client.get("/app.js").status_code == 404


@pytest.mark.parametrize("name", [".env", "backup.db", "file.mp4.bak", ".hidden.mp4", ".private/file.mp4"])
def test_archive_rejects_non_media_and_hidden_paths_before_any_probe(tmp_path, monkeypatch, name):
    root = get_settings().recordings_dir
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"SYNTHETIC_SECRET_MARKER")
    def forbidden(*_):
        raise AssertionError("must reject before probing, conversion or naming")
    monkeypatch.setattr(recordings.playback, "cached_path", forbidden)
    monkeypatch.setattr(recordings, "_recording_download_name", forbidden)
    app = FastAPI(); app.include_router(recordings.router)
    with TestClient(app) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        for method, route in [("GET", "file"), ("GET", "download"), ("GET", "playback-status"), ("POST", "prepare")]:
            response = client.request(method, "/api/recordings/" + route, params={"path": str(target), "original": "true"})
            assert response.status_code == 404 and "SYNTHETIC_SECRET_MARKER" not in response.text


def test_docker_context_defense_covers_common_secret_and_backup_names():
    root = Path(__file__).parents[1]
    rules = set((root / ".dockerignore").read_text().splitlines())
    assert {".env*", "**/.env*", "**/.git", "**/*.pem", "**/*.key", "**/*.db", "**/*.sqlite",
            "**/*.bak", "**/*.old", "re/", "temp/", "recordings/", "backups/"} <= rules
    dockerfile = (root / "Dockerfile").read_text()
    assert "COPY . " not in dockerfile
