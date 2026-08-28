"""Camera registry: the durable list of cameras the user has configured.

Every row is keyed by an opaque public ``camera_id`` (ADR 0027). MAC is an optional native/discovery
identifier with a compatibility lookup, not the storage identity. Drivers translate the stable ID
to their own MAC, serial, vendor device ID, certificate or other native identity. Each record carries
credentials and the confirmed RTSP path; IP remains only a mutable last-seen discovery address.

Passwords are encrypted at rest with Fernet. The key is derived from the dashboard secret
in ``.env`` (see ``config``), so the DB file alone never leaks credentials. Note: because
the key is derived from that secret, rotating the secret invalidates stored passwords and
they must be re-entered — an intentional trade-off for a local-first prototype.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken

from ..camera_identity import stable_camera_id, valid_camera_id
from ..config import get_settings
from ..discovery.active_scan import ScannedHost
from . import connect

log = logging.getLogger(__name__)

DEFAULT_USERNAME = "admin"
DEFAULT_RTSP_PORT = 554

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    camera_id    TEXT PRIMARY KEY,
    mac          TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL DEFAULT '',
    username     TEXT NOT NULL DEFAULT 'admin',
    password_enc BLOB,
    stream_path  TEXT NOT NULL DEFAULT '',
    rtsp_port    INTEGER NOT NULL DEFAULT 554,
    last_ip      TEXT NOT NULL DEFAULT '',
    vendor       TEXT NOT NULL DEFAULT '',
    capabilities TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

# Columns added after the initial release, applied idempotently on init for existing DBs.
_MIGRATIONS = {
    "capabilities": "ALTER TABLE cameras ADD COLUMN capabilities TEXT NOT NULL DEFAULT ''",
    "camera_id": "ALTER TABLE cameras ADD COLUMN camera_id TEXT NOT NULL DEFAULT ''",
}


@dataclass
class Camera:
    mac: str = ""
    name: str = ""
    username: str = DEFAULT_USERNAME
    password: str = ""  # decrypted; never persisted in the clear
    stream_path: str = ""
    rtsp_port: int = DEFAULT_RTSP_PORT
    last_ip: str = ""
    vendor: str = ""
    capabilities: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    camera_id: str = ""

    @property
    def rtsp_url(self) -> str | None:
        """Full RTSP URL to the confirmed stream, or None if not enough is known."""
        if not self.last_ip or not self.stream_path:
            return None
        cred = ""
        if self.username:
            cred = f"{self.username}:{self.password}@" if self.password else f"{self.username}@"
        return f"rtsp://{cred}{self.last_ip}:{self.rtsp_port}{self.stream_path}"

    @property
    def substream_url(self) -> str | None:
        """RTSP URL of the camera's *secondary* (low-resolution) stream, if it has one.

        IP cameras publish a main stream for recording and a small substream for live viewing;
        the probe stores both in ``capabilities['stream_paths']`` (main first). Returns None
        when the camera only advertises one path, so callers fall back to the main stream.
        """
        paths = [p for p in (self.capabilities.get("stream_paths") or []) if p != self.stream_path]
        if not paths or not (main := self.rtsp_url):
            return None
        return main[: -len(self.stream_path)] + paths[0]


# --- crypto ------------------------------------------------------------------------

def _fernet() -> Fernet:
    secret = get_settings().effective_signing_key.encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def _encrypt(password: str) -> bytes:
    return _fernet().encrypt(password.encode("utf-8"))


def _decrypt(token: bytes | None, *, label: str = "camera password") -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(bytes(token)).decode("utf-8")
    except InvalidToken:
        # A stored password exists but won't decrypt — almost always because
        # DASHBOARD_SECRET_KEY changed since it was saved. Surface it (streams would
        # silently lose auth otherwise) and treat as no stored password.
        log.warning("stored %s failed to decrypt — was DASHBOARD_SECRET_KEY changed? "
                    "Re-enter/re-enroll the credential to fix.", label)
        return ""


# --- connection --------------------------------------------------------------------

# ``connect()`` (shared, from the db package) opens the app database; aliased so the
# existing call sites below read naturally.
_connect = connect


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(cameras)")}
        for column, ddl in _MIGRATIONS.items():
            if column not in existing:
                conn.execute(ddl)
        for row in conn.execute("SELECT mac FROM cameras WHERE camera_id = ''"):
            conn.execute(
                "UPDATE cameras SET camera_id = ? WHERE mac = ?",
                (stable_camera_id("mac", row["mac"]), row["mac"]),
            )
        primary_key = next(
            (row["name"] for row in conn.execute("PRAGMA table_info(cameras)") if row["pk"]),
            "",
        )
        if primary_key != "camera_id":
            # SQLite cannot alter a primary key in place. Rebuild transactionally after old rows
            # have received their deterministic IDs; encrypted blobs and timestamps are copied
            # byte-for-byte.
            conn.executescript(
                """BEGIN IMMEDIATE;
                   CREATE TABLE cameras_v2 (
                       camera_id TEXT PRIMARY KEY,
                       mac TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
                       username TEXT NOT NULL DEFAULT 'admin', password_enc BLOB,
                       stream_path TEXT NOT NULL DEFAULT '', rtsp_port INTEGER NOT NULL DEFAULT 554,
                       last_ip TEXT NOT NULL DEFAULT '', vendor TEXT NOT NULL DEFAULT '',
                       capabilities TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   );
                   INSERT INTO cameras_v2
                       (camera_id, mac, name, username, password_enc, stream_path, rtsp_port,
                        last_ip, vendor, capabilities, created_at, updated_at)
                   SELECT camera_id, mac, name, username, password_enc, stream_path, rtsp_port,
                          last_ip, vendor, capabilities, created_at, updated_at FROM cameras;
                   DROP TABLE cameras;
                   ALTER TABLE cameras_v2 RENAME TO cameras;
                   COMMIT;"""
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cameras_camera_id ON cameras(camera_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cameras_mac "
            "ON cameras(mac) WHERE mac <> ''"
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _load_caps(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _row_to_camera(row: sqlite3.Row) -> Camera:
    return Camera(
        mac=row["mac"],
        name=row["name"],
        username=row["username"],
        password=_decrypt(row["password_enc"]),
        stream_path=row["stream_path"],
        rtsp_port=row["rtsp_port"],
        last_ip=row["last_ip"],
        vendor=row["vendor"],
        capabilities=_load_caps(row["capabilities"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        camera_id=row["camera_id"],
    )


# --- CRUD --------------------------------------------------------------------------

def list_cameras() -> list[Camera]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM cameras ORDER BY name, mac").fetchall()
    return [_row_to_camera(r) for r in rows]


def get_camera(mac: str) -> Camera | None:
    if not mac:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE mac = ?", (mac.lower(),)).fetchone()
    return _row_to_camera(row) if row else None


def get_camera_by_id(camera_id: str) -> Camera | None:
    if not valid_camera_id(camera_id):
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM cameras WHERE camera_id = ?", (str(camera_id),)
        ).fetchone()
    return _row_to_camera(row) if row else None


def upsert_camera(mac: str = "", *, name: str | None = None, username: str | None = None,
                  password: str | None = None, stream_path: str | None = None,
                  rtsp_port: int | None = None, last_ip: str | None = None,
                  vendor: str | None = None, capabilities: dict | None = None,
                  identity_kind: str = "mac", identity_value: str | None = None,
                  camera_id: str | None = None) -> Camera:
    """Insert or update a camera by canonical identity. Only provided fields are changed.

    MAC remains the normal discovery lookup but may be empty when a driver has another durable
    identity. ``password`` is encrypted before storage. Pass an empty string to clear it.
    """
    mac = str(mac or "").lower()
    if camera_id is not None:
        if not valid_camera_id(camera_id):
            raise ValueError("camera_id is invalid")
        derived_id = camera_id
    else:
        identity_source = identity_value or mac
        if not identity_source:
            raise ValueError("a durable camera identity is required")
        derived_id = stable_camera_id(identity_kind, identity_source)
    now = _now()
    existing = get_camera_by_id(derived_id)
    if existing is None:
        existing = get_camera(mac) if mac else None

    if existing is None:
        cam = Camera(
            mac=mac,
            created_at=now,
            updated_at=now,
            camera_id=derived_id,
        )
    else:
        cam = existing
        cam.updated_at = now
        if mac:
            cam.mac = mac

    if name is not None:
        cam.name = name
    if username is not None:
        cam.username = username
    if password is not None:
        cam.password = password
    if stream_path is not None:
        cam.stream_path = stream_path
    if rtsp_port is not None:
        cam.rtsp_port = rtsp_port
    if last_ip is not None:
        cam.last_ip = last_ip
    if vendor is not None:
        cam.vendor = vendor
    if capabilities is not None:
        cam.capabilities = capabilities

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO cameras (mac, camera_id, name, username, password_enc, stream_path,
                                 rtsp_port, last_ip, vendor, capabilities,
                                 created_at, updated_at)
            VALUES (:mac, :camera_id, :name, :username, :password_enc, :stream_path,
                    :rtsp_port, :last_ip, :vendor, :capabilities,
                    :created_at, :updated_at)
            ON CONFLICT(camera_id) DO UPDATE SET
                mac=excluded.mac, name=excluded.name, username=excluded.username,
                password_enc=excluded.password_enc, stream_path=excluded.stream_path,
                rtsp_port=excluded.rtsp_port, last_ip=excluded.last_ip,
                vendor=excluded.vendor, capabilities=excluded.capabilities,
                updated_at=excluded.updated_at
            """,
            {
                "mac": cam.mac, "camera_id": cam.camera_id,
                "name": cam.name, "username": cam.username,
                "password_enc": _encrypt(cam.password) if cam.password else None,
                "stream_path": cam.stream_path, "rtsp_port": cam.rtsp_port,
                "last_ip": cam.last_ip, "vendor": cam.vendor,
                "capabilities": json.dumps(cam.capabilities) if cam.capabilities else "",
                "created_at": cam.created_at, "updated_at": cam.updated_at,
            },
        )
    return cam


def delete_camera(mac: str) -> None:
    """Deprecated exact-MAC deletion retained for old internal callers."""
    if not mac:
        return  # empty MAC is valid for multiple canonical rows and must never become bulk delete
    with _connect() as conn:
        conn.execute("DELETE FROM cameras WHERE mac = ?", (mac.lower(),))


def delete_camera_by_id(camera_id: str) -> None:
    if not valid_camera_id(camera_id):
        return
    with _connect() as conn:
        conn.execute("DELETE FROM cameras WHERE camera_id = ?", (camera_id,))


def rekey_camera(old_mac: str, new_mac: str) -> Camera | None:
    """Move a registered camera to a new MAC, keeping name, credentials and capabilities.

    A camera first registered under its **ARP-derived** MAC keeps that key forever, even once a
    later scan learns the authoritative MAC the camera reports over ONVIF (docs/DECISIONS.md §23).
    Without this the same physical camera comes back as a brand-new candidate while the original
    record — with its name, password and capabilities — goes stale and un-matchable.

    Returns the moved camera, or None when there is nothing safe to do: the source is unknown, or
    the target MAC is **already registered**, which means a genuinely different camera — records
    are never merged, since that would silently discard one camera's credentials.
    """
    old_mac, new_mac = old_mac.lower(), new_mac.lower()
    if old_mac == new_mac:
        return get_camera(old_mac)
    cam = get_camera(old_mac)
    if cam is None or get_camera(new_mac) is not None:
        return None
    cam.mac, cam.updated_at = new_mac, _now()
    with _connect() as conn:
        conn.execute(
            "UPDATE cameras SET mac = ?, updated_at = ? WHERE camera_id = ?",
            (cam.mac, cam.updated_at, cam.camera_id),
        )
    log.info("camera re-keyed %s -> %s (authoritative ONVIF MAC)", old_mac, new_mac)
    return cam


# --- discovery reconciliation ------------------------------------------------------

@dataclass
class Candidate:
    """A scanned host not yet in the registry — surfaced for the user to configure."""

    mac: str
    ip: str
    open_ports: list[int]
    suggested_path: str = ""      # a stream path (RTSP DESCRIBE 200, else ONVIF-reported)
    suggested_username: str = DEFAULT_USERNAME
    # No-auth ONVIF identity (filled at scan time, before the user adds a password).
    vendor: str = ""
    model: str = ""
    firmware: str = ""
    driver: str = ""


def reconcile(hosts: list[ScannedHost],
              *, on_rekey: Callable[[str, str], None] | None = None,
              ) -> tuple[list[Camera], list[Candidate]]:
    """Match a fresh scan against the registry.

    Known MACs get their ``last_ip`` refreshed (handling DHCP churn) and are returned as
    updated ``Camera`` records. Unknown MACs with RTSP become ``Candidate`` entries for
    the dashboard's "add camera" list. Hosts without a resolvable MAC are skipped (we
    can't give them a stable identity).

    A host whose authoritative (ONVIF) MAC is unknown but whose **ARP** MAC is registered is the
    same camera seen under a better identity, so it is **re-keyed** in place rather than offered
    as a new candidate (see :func:`rekey_camera`). ``on_rekey(old, new)`` is invoked for each move
    so the caller can migrate what else is keyed by MAC — recordings, chiefly; the registry itself
    doesn't reach into the recording layer.
    """
    configured: list[Camera] = []
    candidates: list[Candidate] = []
    for host in hosts:
        if not host.mac:
            continue
        existing = get_camera(host.mac)
        if existing is None and host.arp_mac and host.arp_mac != host.mac:
            moved = rekey_camera(host.arp_mac, host.mac)
            if moved is not None:
                existing = moved
                if on_rekey is not None:
                    on_rekey(host.arp_mac, host.mac)
        if existing is not None:
            configured.append(upsert_camera(host.mac, last_ip=host.address))
        elif host.has_rtsp:
            working = next((s.url for s in host.working_streams), "")
            suggested_path = ""
            if working:  # extract the path portion from rtsp://host:port/path
                suggested_path = "/" + working.split("/", 3)[3] if working.count("/") >= 3 else ""
            elif host.stream_paths:  # no authed stream yet — use the ONVIF-reported path
                suggested_path = host.stream_paths[0]
            candidates.append(Candidate(
                mac=host.mac, ip=host.address, open_ports=host.open_ports,
                suggested_path=suggested_path,
                vendor=host.vendor, model=host.model,
                firmware=host.firmware, driver=host.driver,
            ))
    return configured, candidates
