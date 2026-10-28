"""Package one committed source revision, never the working tree or private data.

Termux consumes this source artifact, not an OCI/Docker image. Installation and
activation are separate operations so building cannot touch the running server.
"""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def package(root: Path, revision: str, output: Path) -> Path:
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=root, text=True,
    ).strip()
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("a committed Git revision is required")
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / f"ccg-{commit}.tar"
    # A new artifact only: never overwrite a release already being transferred.
    with artifact.open("xb") as stream:
        subprocess.run([
            "git", "archive", "--format=tar", commit, "backend", "frontend",
            "build_tools", "pyproject.toml", "README.md", "LICENSE", ".env.example",
        ], cwd=root, stdout=stream, check=True)
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = artifact.with_suffix(".json")
    with manifest.open("x") as stream:
        json.dump({"revision": commit, "sha256": digest, "artifact": artifact.name,
                   "format": "source-tar", "requires": "Python >=3.11, FFmpeg, go2rtc"}, stream)
        stream.write("\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="Validated committed revision")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(package(Path(__file__).resolve().parents[1], args.revision, args.output))


if __name__ == "__main__":
    main()
