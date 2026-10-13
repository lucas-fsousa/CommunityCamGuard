"""Cancellable system DNS for the experimental account opener, no authentication."""

import ipaddress
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .contracts import P2PProbeError

LIST_HOST = "list.iotvideo.tencentcs.com"
LIST_PORT = 51701
_gate = threading.Lock()
_blocked = False


def validate_hosts(value: object) -> tuple[str, ...]:
    """Bound a trusted resolver result; never accept hostnames for a second lookup."""
    if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 16:
        raise P2PProbeError("invalid list-service resolver result")
    hosts = []
    for item in value:
        if not isinstance(item, str):
            raise P2PProbeError("invalid list-service resolver result")
        try:
            hosts.append(str(ipaddress.IPv4Address(item)))
        except ipaddress.AddressValueError:
            raise P2PProbeError("invalid list-service resolver result") from None
    return tuple(dict.fromkeys(hosts))


def resolve_list_hosts(deadline: float, *,
                       cancelled: Callable[[], bool] = lambda: False) -> tuple[str, ...]:
    """One process-wide lookup, no queue/cache/retry, <=5s plus bounded cleanup.

    Uses system NSS/DNS configuration. No cloud/camera login or inherited app
    environment in the child. Process creation itself remains an OS operation;
    after spawn, check the original deadline before waiting or accepting results.
    Unconfirmed child exit blocks subsequent lookups instead of leaking workers.
    """
    global _blocked
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise ValueError("finite resolver deadline required")
    if not _gate.acquire(blocking=False):
        raise P2PProbeError("list-service resolver busy")
    child = None
    try:
        if _blocked:
            raise P2PProbeError("list-service resolver cleanup unconfirmed")
        end = min(deadline, time.monotonic() + 5.0)

        def check() -> float:
            remaining = end - time.monotonic()
            if remaining <= 0 or cancelled():
                raise P2PProbeError("list-service resolver deadline or cancellation")
            return remaining

        check()
        environment = {key: os.environ[key] for key in ("SYSTEMROOT",) if key in os.environ}
        child = subprocess.Popen(
            [sys.executable, "-I", "-S", str(Path(__file__).with_name("list_dns_child.py")),
             LIST_HOST, str(LIST_PORT)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=environment, close_fds=True,
        )
        while True:
            try:
                output, _ = child.communicate(timeout=min(0.05, check()))
                break
            except subprocess.TimeoutExpired:
                continue
        check()
        if child.returncode != 0 or len(output) > 1024:
            raise P2PProbeError("list-service resolver failed")
        try:
            decoded = json.loads(output)
        except (ValueError, UnicodeError):
            raise P2PProbeError("invalid list-service resolver result") from None
        return validate_hosts(decoded)
    except OSError:
        raise P2PProbeError("list-service resolver unavailable") from None
    finally:
        try:
            if child is not None:
                try:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=0.5)
                except BaseException as exc:
                    _blocked = True
                    if isinstance(exc, (OSError, subprocess.TimeoutExpired)):
                        raise P2PProbeError("list-service resolver cleanup unconfirmed") from None
                    raise
                finally:
                    if child.stdout is not None:
                        child.stdout.close()
        finally:
            _gate.release()
