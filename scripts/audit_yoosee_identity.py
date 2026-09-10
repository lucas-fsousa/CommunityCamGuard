"""Offline, bounded identity audit. Run as python -m scripts.audit_yoosee_identity.

Explicit files only; no network, writes, profile import or credential output.
Device IDs are fingerprinted. Optional SQLite access is strictly read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from backend.app.drivers.yoosee.capability_identity import normalize_identity
from backend.app.drivers.yoosee.p2p.contracts import P2PPropertyRead

MAX_LINE = 1024 * 1024
MAX_FILE = 32 * MAX_LINE
PREFIX = "[MSG<-DEV onUpdateProperty] "


def parse_line(line: str):
    """Accept only an explicitly device-addressed, complete ProConst callback."""
    if PREFIX not in line:
        return None
    parts = line.split(PREFIX, 1)[1].rstrip().rsplit(" | ", 2)
    if len(parts) != 3 or parts[1] != "ProConst":
        return None
    payload, _, device = parts
    try:
        value = json.loads(payload)
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    reads = tuple(
        P2PPropertyRead(device, "ProConst." + key, True, False, False, 0, value.get(key))
        for key in ("_productInfo", "_versionInfo")
    )
    # The flags above adapt a historical callback to the pure schema normalizer;
    # they do NOT certify a current authenticated/correlated camera session.
    return normalize_identity(*reads, device_id=device)


def associations(database: Path) -> dict[str, str]:
    conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return dict(conn.execute("SELECT device_id, camera_id FROM p2p_enrollments"))
    finally:
        conn.close()


def audit(path: Path, linked: dict[str, str]):
    if path.stat().st_size > MAX_FILE:
        raise ValueError("capture exceeds 32 MiB audit budget")
    with path.open("rb") as handle:
        line_number = 0
        consumed = 0
        while raw := handle.readline(MAX_LINE + 1):
            line_number += 1
            consumed += len(raw)
            if len(raw) > MAX_LINE or consumed > MAX_FILE:
                raise ValueError("capture exceeds audit budget")
            identity = parse_line(raw.decode("utf-8", errors="replace"))
            if identity is None:
                continue
            safe = asdict(identity)
            device = safe.pop("device_id")
            yield {
                "source": path.name,
                "line": line_number,
                "device_fingerprint": hashlib.sha256(device.encode()).hexdigest()[:16],
                "camera_id": linked.get(device) or None,
                "identity": safe,
                "historical_only": True,
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    if len(args.captures) > 8:
        parser.error("at most eight explicit captures per audit")
    linked = associations(args.database) if args.database else {}
    for path in args.captures:
        for record in audit(path, linked):
            print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
