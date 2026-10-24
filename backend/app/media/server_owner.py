"""OS-held single-server ownership for one canonical database path, not a PID file."""

import errno
import os
import stat
import sys
import threading
from pathlib import Path
from typing import Any

from .source_registry import SourceCleanupUnconfirmed, SourceUnavailable


class ServerOwnerBusy(SourceUnavailable):
    pass


def _windows_lock(fd: int) -> None:
    import msvcrt
    if os.fstat(fd).st_size == 0:
        os.write(fd, b"\0")  # fixed lock byte, never identity or credentials
    os.lseek(fd, 0, os.SEEK_SET)
    # Platform-specific stubs omit these members on POSIX.
    windows: Any = msvcrt
    windows.locking(fd, windows.LK_NBLCK, 1)


def _lock(fd: int) -> None:
    if os.name == "posix":
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif sys.platform == "win32":
        _windows_lock(fd)
    else:
        raise SourceUnavailable("server ownership locking unavailable")


class ServerOwner:
    def __init__(self, database: Path):
        canonical = database.resolve()
        self._path = canonical.with_name(canonical.name + ".source-owner.lock")
        self._lock = threading.Lock()
        self._fd: int | None = None
        self._used = False
        self._release_failed = False

    @property
    def acquired(self) -> bool:
        with self._lock:
            return self._fd is not None

    def acquire(self) -> None:
        with self._lock:
            if self._used:
                raise SourceUnavailable("server owner handle already used")
            self._used = True
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = None
            try:
                flags = (os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
                         | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                fd = os.open(self._path, flags, 0o600)
                os.set_inheritable(fd, False)
                opened, named = os.fstat(fd), self._path.lstat()
                if (not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(named.st_mode)
                        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
                        or opened.st_nlink != 1):
                    raise SourceUnavailable("unsafe server ownership file")
                if os.name == "posix" and (opened.st_uid != os.geteuid() or stat.S_IMODE(opened.st_mode) & 0o077):
                    raise SourceUnavailable("private server ownership file required")
                _lock(fd)
                current = self._path.lstat()
                if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                    raise SourceUnavailable("server ownership file changed")
                self._fd, fd = fd, None
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                    raise ServerOwnerBusy("another server owns this database or ownership access is denied") from None
                raise SourceUnavailable("server ownership lock unavailable") from None
            finally:
                if fd is not None:
                    os.close(fd)

    def close(self) -> None:
        """Only after all services confirmed cleanup. Never unlink the lock inode."""
        with self._lock:
            if self._fd is None:
                return
            if self._release_failed:
                raise SourceCleanupUnconfirmed("server ownership release requires process termination")
            try:
                os.close(self._fd)  # releases the OS lock, including after process death
            except OSError:
                self._release_failed = True  # never close a possibly recycled FD on retry
                raise SourceCleanupUnconfirmed("server ownership release unconfirmed") from None
            self._fd = None
