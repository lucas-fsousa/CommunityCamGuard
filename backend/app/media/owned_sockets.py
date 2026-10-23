"""Discover the one loopback TCP listener belonging to an unreaped owned child.

Never probe a candidate port: correlate the child's socket inodes with procfs.
Caller retains the Popen handle to prevent PID reuse during this inspection.
"""

import os
import sys
from pathlib import Path

MAX_FDS = 128
MAX_ROWS = 8192
LOOPBACK_HEX = f"{int.from_bytes(bytes((127, 0, 0, 1)), sys.byteorder):08X}"


def loopback_listener(pid: int) -> int | None:
    if type(pid) is not int or pid <= 1:
        raise ValueError("owned worker PID required")
    inodes = set()
    try:
        for index, fd in enumerate(Path(f"/proc/{pid}/fd").iterdir()):
            if index >= MAX_FDS:
                raise ValueError("worker descriptor budget exceeded")
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue  # runtime descriptor closed during the snapshot
            if target.startswith("socket:[") and target.endswith("]"):
                inodes.add(target[8:-1])
    except FileNotFoundError:
        return None
    listeners = []
    rows = 0
    for table in ("tcp", "tcp6"):
        try:
            with Path(f"/proc/{pid}/net/{table}").open() as stream:
                next(stream, "")
                while line := stream.readline(512):
                    rows += 1
                    if rows > MAX_ROWS or not line.endswith("\n"):
                        raise ValueError("worker socket inspection budget exceeded")
                    fields = line.split()
                    if len(fields) < 10:
                        raise ValueError("invalid worker socket table")
                    if fields[3] != "0A" or fields[9] not in inodes:
                        continue
                    address, port = fields[1].split(":")
                    if table != "tcp" or address != LOOPBACK_HEX or int(port, 16) == 0:
                        raise ValueError("worker listener must use IPv4 loopback")
                    listeners.append(int(port, 16))
        except FileNotFoundError:
            if table == "tcp":
                return None
    if len(listeners) > 1:
        raise ValueError("unexpected extra worker listener")
    return listeners[0] if listeners else None
