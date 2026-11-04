"""Linux PID-namespace init lifetime: its death retires every process in that namespace."""

import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class InitScope:
    boot: str
    namespace: int
    started: int


def decode(value: bytes) -> InitScope | None:
    if len(value) > 2048:
        return None
    try:
        data = json.loads(value)
        if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
            return None
        item = InitScope(**data['scope'])
        if (not isinstance(item.boot, str) or not re.fullmatch(r'[0-9a-f-]{36}', item.boot)
                or type(item.namespace) is not int or item.namespace <= 0
                or type(item.started) is not int or item.started <= 0):
            return None
        return item
    except (ValueError, TypeError, KeyError):
        return None


def current() -> InitScope | None:
    # Do not infer orphan retirement for arbitrary host processes or --init layouts.
    if not sys.platform.startswith('linux') or os.getpid() != 1:
        return None
    try:
        fields = Path('/proc/1/stat').read_text().rsplit(') ', 1)[1].split()
        item = InitScope(Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                         Path('/proc/1/ns/pid').stat().st_ino, int(fields[19]))
        return decode(encode(item))
    except (OSError, ValueError, IndexError):
        return None


def encode(scope: InitScope | None) -> bytes:
    return json.dumps({'version': 1, 'scope': asdict(scope) if scope else None},
                      separators=(',', ':')).encode('ascii')
