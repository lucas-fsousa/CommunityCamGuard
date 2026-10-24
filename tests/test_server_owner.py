"""OS ownership tests with private temporary paths and tiny camera-free children."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.media import server_owner
from backend.app.media.server_owner import ServerOwner, ServerOwnerBusy
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable


def test_owner_is_exclusive_noninherited_and_keeps_same_lock_inode(tmp_path):
    database = tmp_path / "app.db"
    owner = ServerOwner(database)
    owner.acquire()
    inode = owner._path.stat().st_ino
    try:
        assert owner.acquired and not os.get_inheritable(owner._fd)
        with pytest.raises(ServerOwnerBusy):
            ServerOwner(database).acquire()
        assert not database.exists()  # lock acquisition does not initialize SQLite
    finally:
        owner.close()
    owner.close()
    assert owner._path.is_file() and owner._path.stat().st_ino == inode
    replacement = ServerOwner(database)
    replacement.acquire()
    replacement.close()
    with pytest.raises(SourceUnavailable):
        owner.acquire()  # a lease object is single-use


def test_canonical_database_alias_shares_the_same_owner(tmp_path):
    if os.name != "posix":
        pytest.skip("POSIX symlink fixture")
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    owner = ServerOwner(actual / "app.db")
    owner.acquire()
    try:
        with pytest.raises(ServerOwnerBusy):
            ServerOwner(alias / "app.db").acquire()
    finally:
        owner.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX file protection")
@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "public", "directory"])
def test_unsafe_lockfile_is_rejected_without_modifying_it(tmp_path, unsafe):
    owner = ServerOwner(tmp_path / "app.db")
    target = tmp_path / "sentinel"
    target.write_text("preserve")
    if unsafe == "symlink":
        owner._path.symlink_to(target)
    elif unsafe == "hardlink":
        os.link(target, owner._path)
    elif unsafe == "public":
        owner._path.write_bytes(b"preserve")
        owner._path.chmod(0o644)
    else:
        owner._path.mkdir()
    with pytest.raises(SourceUnavailable):
        owner.acquire()
    assert target.read_text() == "preserve" and not owner.acquired


def test_lock_failure_closes_only_the_new_descriptor(tmp_path, monkeypatch):
    recorded = []
    def fail(fd):
        recorded.append(fd)
        raise OSError("synthetic locking error")
    monkeypatch.setattr(server_owner, "_lock", fail)
    owner = ServerOwner(tmp_path / "app.db")
    with pytest.raises(SourceUnavailable, match="ownership lock unavailable"):
        owner.acquire()
    assert not owner.acquired
    with pytest.raises(OSError):
        os.fstat(recorded[0])


def test_uncertain_close_is_not_retried_against_a_possibly_recycled_descriptor(tmp_path, monkeypatch):
    owner = ServerOwner(tmp_path / "app.db")
    owner.acquire()
    fd, real_close, calls = owner._fd, os.close, []
    def fail_close(value):
        calls.append(value)
        raise OSError("synthetic unknown close")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(server_owner.os, "close", fail_close)
            with pytest.raises(SourceCleanupUnconfirmed):
                owner.close()
            with pytest.raises(SourceCleanupUnconfirmed):
                owner.close()
        assert calls == [fd]
    finally:
        real_close(fd)  # the fixture fault never actually closed this descriptor


def test_windows_adapter_uses_one_fixed_byte_and_nonblocking_lock(tmp_path, monkeypatch):
    calls = []
    module = SimpleNamespace(LK_NBLCK=7, locking=lambda fd, mode, count: calls.append(
        (mode, count, os.lseek(fd, 0, os.SEEK_CUR))))
    monkeypatch.setitem(sys.modules, "msvcrt", module)
    fd = os.open(tmp_path / "adapter", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        server_owner._windows_lock(fd)
        server_owner._windows_lock(fd)
        assert calls == [(7, 1, 0), (7, 1, 0)] and os.fstat(fd).st_size == 1
    finally:
        os.close(fd)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-death acceptance")
def test_kernel_releases_ownership_after_real_child_process_death(tmp_path):
    database = tmp_path / "app.db"
    code = ("import sys; from pathlib import Path; "
            "from backend.app.media.server_owner import ServerOwner; "
            "owner=ServerOwner(Path(sys.argv[1])); owner.acquire(); "
            "print('owned', flush=True); sys.stdin.buffer.read(1)")
    child = subprocess.Popen([sys.executable, "-c", code, str(database)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             cwd=Path(__file__).resolve().parents[1])
    try:
        # Bound handshake readiness; the independent probe sees the live lock.
        import select
        assert select.select([child.stdout], [], [], 3)[0]
        assert child.stdout.readline() == b"owned\n"
        with pytest.raises(ServerOwnerBusy):
            ServerOwner(database).acquire()
        child.terminate()  # our isolated child owns no camera/socket/media process
        child.wait(timeout=3)
        replacement = ServerOwner(database)
        replacement.acquire()
        replacement.close()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)
        child.stdin.close()
        child.stdout.close()
