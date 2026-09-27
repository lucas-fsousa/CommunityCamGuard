"""Generated credential-bearing config remains owner-only and bind-mount stable."""

import os
import stat

import pytest

from backend.app.media.config_file import write_private_config


def test_new_config_is_owner_only_even_with_permissive_umask(tmp_path):
    target = tmp_path / "nested" / "go2rtc.yaml"
    previous = os.umask(0)
    try:
        assert write_private_config(target, "synthetic credentials")
    finally:
        os.umask(previous)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text() == "synthetic credentials"


def test_unchanged_config_repairs_permissions_without_rewriting(tmp_path):
    target = tmp_path / "config"
    target.write_text("synthetic")
    target.chmod(0o644)
    old = target.stat()
    assert not write_private_config(target, "synthetic", only_if_changed=True)
    current = target.stat()
    assert current.st_ino == old.st_ino and current.st_mtime_ns == old.st_mtime_ns
    assert stat.S_IMODE(current.st_mode) == 0o600


def test_write_preserves_open_reader_inode_and_truncates(tmp_path):
    target = tmp_path / "config"
    target.write_text("longer synthetic config")
    with target.open() as mounted_reader:
        assert write_private_config(target, "short", only_if_changed=True)
        assert mounted_reader.read() == "short"
    assert target.read_text() == "short"


def test_symlink_rejected_without_touching_target(tmp_path):
    target = tmp_path / "other"
    target.write_text("untouched")
    link = tmp_path / "config"
    link.symlink_to(target)
    with pytest.raises(OSError):
        write_private_config(link, "replacement")
    assert target.read_text() == "untouched"


def test_non_regular_target_rejected_without_blocking(tmp_path):
    target = tmp_path / "pipe"
    os.mkfifo(target)
    with pytest.raises(ValueError, match="regular file"):
        write_private_config(target, "synthetic")


def test_invalid_old_encoding_is_replaced(tmp_path):
    target = tmp_path / "config"
    target.write_bytes(b"\xff\xfe")
    assert write_private_config(target, "valid", only_if_changed=True)
    assert target.read_text() == "valid"


def test_permission_failure_never_writes(tmp_path, monkeypatch):
    target = tmp_path / "config"
    target.write_text("old")

    def denied(*args):
        raise PermissionError("synthetic")

    monkeypatch.setattr(os, "fchmod", denied)
    with pytest.raises(PermissionError):
        write_private_config(target, "new")
    assert target.read_text() == "old"
