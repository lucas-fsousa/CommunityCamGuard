"""Minimal RTSP-only worker configuration, sealed in memory on Linux."""

import fcntl
import json
import os
import re
from urllib.parse import urlsplit

MAX_CONFIG_BYTES = 16384


def worker_config(source_url: str, *, stream_name: str) -> bytes:
    if type(stream_name) is not str or not re.fullmatch(r"ingress_[0-9a-f]{32}", stream_name):
        raise ValueError("generation-specific worker stream name required")
    if (type(source_url) is not str or not 1 <= len(source_url) <= 4096
            or not source_url.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in source_url)
            or any(c in source_url for c in "#{}\\")):
        raise ValueError("canonical RTSP URL required")
    try:
        parts = urlsplit(source_url)
        valid = parts.scheme in {"rtsp", "rtsps"} and parts.hostname and parts.port != 0
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("canonical RTSP URL required") from None
    config = {
        "app": {"modules": ["rtsp"]},
        "api": {"listen": ""},
        "webrtc": {"listen": ""},
        "rtsp": {"listen": "127.0.0.1:0"},
        "streams": {stream_name: source_url + "#backchannel=0"},
        "log": {"level": "disabled"},
    }
    data = json.dumps(config, separators=(",", ":")).encode("ascii")
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError("RTSP worker configuration too large")
    return data


def sealed_config(data: bytes) -> int:
    if type(data) is not bytes or not 0 < len(data) <= MAX_CONFIG_BYTES:
        raise ValueError("bounded worker configuration required")
    fd = os.memfd_create("ccg-rtsp-ingress", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
        os.lseek(fd, 0, os.SEEK_SET)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
                    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        return fd
    except BaseException:
        os.close(fd)
        raise
