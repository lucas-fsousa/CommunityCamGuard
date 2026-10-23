"""Linux single-process exec wrapper; never imported into the threaded server.

Arm parent death before replacing this interpreter with the trusted executable.
No Python preexec_fn runs in the server's forked child, and no supervisor remains.
"""

import ctypes
import errno
import os
import resource
import signal
import stat
import sys


def guarded_exec(parent: int, command: list[str]) -> None:
    if sys.platform != "linux" or parent <= 0 or not command or os.getppid() != parent:
        raise RuntimeError("worker parent unavailable")
    mode = os.stat(command[0]).st_mode
    if not stat.S_ISREG(mode) or mode & (stat.S_ISUID | stat.S_ISGID):
        raise RuntimeError("unprivileged executable required")
    try:
        capabilities = os.getxattr(command[0], "security.capability")
    except OSError as exc:
        if exc.errno not in {errno.ENODATA, errno.ENOTSUP}:
            raise
    else:
        if capabilities:
            raise RuntimeError("unprivileged executable required")
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    if prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "worker parent-death guard unavailable")
    if os.getppid() != parent:
        raise RuntimeError("worker parent changed")
    _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    descriptors = 128 if hard == resource.RLIM_INFINITY else min(128, hard)
    resource.setrlimit(resource.RLIMIT_NOFILE, (descriptors, descriptors))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.execv(command[0], command)


if __name__ == "__main__":
    try:
        guarded_exec(int(sys.argv[1]), sys.argv[2:])
    except Exception:
        # Neither credentials/config nor an exception containing argv reach logs.
        raise SystemExit(70) from None
