"""Install a pinned Linux go2rtc artifact for the Docker image and CI fixtures.

Standard library only. Never overwrite an existing different file, follow output
symlinks, execute a downloaded artifact, or publish bytes before digest validation.
"""

import argparse
import hashlib
import os
import platform
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

VERSION = "1.9.14"
ARTIFACTS = {
    "amd64": ("go2rtc_linux_amd64", "32d616af226bd731678ffde328b94cfb94e30339bfefc469cfb76323144615a6"),
    "arm64": ("go2rtc_linux_arm64", "359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50"),
}
MAX_BYTES = 32 * 1024 * 1024
CHUNK_BYTES = 65536


def architecture(value: str = "") -> str:
    name = (value or platform.machine()).lower()
    name = {"x86_64": "amd64", "aarch64": "arm64"}.get(name, name)
    if name not in ARTIFACTS:
        raise ValueError("go2rtc artifact supports Linux amd64 and arm64")
    return name


def matches(path: Path, expected: str) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        return False
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_BYTES:
                return False
            digest.update(chunk)
    return digest.hexdigest() == expected


def reuse(path: Path, expected: str) -> bool:
    if not matches(path, expected):
        return False
    mode = path.stat().st_mode
    if not mode & 0o100:
        path.chmod((mode & 0o777) | 0o100)
    return True


def install(value: str, output: Path, *, opener=urllib.request.urlopen,
            clock=time.monotonic) -> str:
    name = architecture(value)
    filename, expected = ARTIFACTS[name]
    if output.is_symlink() or output.exists():
        if reuse(output, expected):
            return name
        raise ValueError("existing output differs from pinned artifact; choose a new path")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, filename_tmp = tempfile.mkstemp(prefix=".go2rtc-download-", dir=output.parent)
    temporary = Path(filename_tmp)
    try:
        digest = hashlib.sha256()
        total = 0
        deadline = clock() + 60
        url = f"https://github.com/AlexxIT/go2rtc/releases/download/v{VERSION}/{filename}"
        with os.fdopen(fd, "wb") as stream, opener(url, timeout=10) as response:
            # HTTP read1 performs one underlying read, so trickling responses
            # cannot hide indefinitely inside a read(n) fill loop.
            read = getattr(response, "read1", response.read)
            while chunk := read(CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_BYTES or clock() > deadline:
                    raise ValueError("artifact transfer budget exceeded")
                digest.update(chunk)
                stream.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("go2rtc artifact checksum mismatch")
        temporary.chmod(0o755)
        try:
            os.link(temporary, output)  # atomic publish, never replace a concurrent user's file
        except FileExistsError:
            if not reuse(output, expected):
                raise ValueError("output changed during artifact download") from None
        return name
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        name = install(args.arch, args.output)
    except (OSError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"go2rtc artifact install failed: {detail}", file=sys.stderr)
        raise SystemExit(1) from None
    print(f"go2rtc {VERSION} ready ({name})")


if __name__ == "__main__":
    main()
