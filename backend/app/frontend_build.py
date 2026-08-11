"""Deterministic application build identity derived from the code being served."""
from __future__ import annotations

import hashlib
from pathlib import Path

_FRONTEND_SUFFIXES = {".css", ".html", ".js"}


def build_version(project_root: Path, frontend_dir: Path) -> str:
    """Return a short content hash for runtime backend + frontend sources.

    There is deliberately no counter or generated tracked file. Two checkouts with identical code
    get the same ID, and any contributor/PR changing executable code gets a new one automatically.
    Reading this small source set once per dashboard load is cheap and also supports the compose
    bind mount: a frontend edit receives a new cache key without rebuilding the app container.
    """
    root = project_root.resolve()
    frontend = frontend_dir.resolve()
    sources: list[tuple[str, Path]] = []

    backend_dir = root / "backend" / "app"
    if backend_dir.is_dir():
        sources.extend(
            ("backend/" + path.relative_to(backend_dir).as_posix(), path)
            for path in backend_dir.rglob("*.py")
            if path.is_file()
        )
    if frontend.is_dir():
        sources.extend(
            ("frontend/" + path.relative_to(frontend).as_posix(), path)
            for path in frontend.rglob("*")
            if path.is_file() and path.suffix.lower() in _FRONTEND_SUFFIXES
        )
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        sources.append(("pyproject.toml", pyproject))

    digest = hashlib.sha256()
    for label, path in sorted(sources):
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "b-" + digest.hexdigest()[:12]
