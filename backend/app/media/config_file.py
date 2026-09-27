"""Owner-only media configuration writes preserving Docker file-bind identity."""

import os
import stat
from pathlib import Path


def write_private_config(path: Path, rendered: str, *, only_if_changed: bool = False) -> bool:
    """Restrict before writing; don't replace an inode mounted by another container."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("media configuration target must be a regular file")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as stream:
            fd = -1  # fdopen owns the descriptor, including on write failure.
            if only_if_changed:
                # Bound comparison memory to the generated config size; an oversized
                # old file must not allocate its full contents merely for comparison.
                try:
                    if stream.read(len(rendered) + 1) == rendered:
                        return False
                except UnicodeDecodeError:
                    pass
            stream.seek(0)
            stream.write(rendered)
            stream.truncate()
        return True
    finally:
        if fd != -1:
            os.close(fd)
