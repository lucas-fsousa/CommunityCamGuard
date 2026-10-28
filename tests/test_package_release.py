"""Source-only release artifacts cannot accidentally include working-tree secrets."""

import json
import subprocess
import tarfile
from pathlib import Path

from scripts.package_release import package


def test_package_uses_commit_not_dirty_or_private_files(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for directory in ("backend", "frontend", "build_tools"):
        (root / directory).mkdir()
        (root / directory / "tracked.txt").write_text("committed")
    for name in ("pyproject.toml", "README.md", "LICENSE", ".env.example"):
        (root / name).write_text("public")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "fixture"], cwd=root, check=True)
    (root / "backend/tracked.txt").write_text("not validated")
    (root / ".env").write_text("private")
    (root / "data").mkdir()
    (root / "data/ccg.db").write_text("private")
    manifest = json.loads(package(root, "HEAD", tmp_path / "out").read_text())
    with tarfile.open(tmp_path / "out" / manifest["artifact"]) as archive:
        assert not any(Path(name).parts[0] in {".env", "data", "re", "recordings", "temp"}
                       for name in archive.getnames())
        assert archive.extractfile("backend/tracked.txt").read() == b"committed"
    assert len(manifest["sha256"]) == 64
