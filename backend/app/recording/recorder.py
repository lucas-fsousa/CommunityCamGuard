"""24/7 chunked recorder.

One ffmpeg per camera copies the camera's **go2rtc restream** (not the camera directly —
go2rtc absorbs the RTSP quirks) into fixed-length segments laid out by day/hour and indexed
in SQLite:

    recordings/<camera_id>/<YYYY-MM-DD>/<HH>/<YYYYMMDD_HHMMSS>.mp4

Every date/time component in that layout is **UTC**.  This is deliberately independent of the
camera timezone, the host timezone and the container's ``TZ`` setting.

Video is remuxed (``-c:v copy`` → near-zero CPU, the whole point). The cameras' PCM A-law
audio is transcoded to AAC (negligible at 16 kHz mono) because MP4 cannot carry A-law.
Small independent segments mean a copy is trivial and a corrupt file costs one minute, not
the whole day.

Each segment is a **fragmented** MP4 (``+frag_keyframe+empty_moov+default_base_moof``) so it
is crash-safe: the index sits at the head and media is flushed as self-contained fragments,
so an abrupt kill (ffmpeg/WSL/host crash) leaves the current segment playable up to its last
fragment. A plain-mp4 segment only gets its trailing ``moov`` on clean finalize, so a crash
would lose the whole in-progress segment.

This ffmpeg's ``segment`` muxer does not create output directories, so a lightweight
maintenance thread pre-creates the current and next hour's directory (covering hour/day
rollovers) and indexes finalized segments into SQLite.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..camera_identity import valid_camera_id
from ..config import get_settings
from ..db import connect, registry
from ..media import go2rtc

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id          INTEGER PRIMARY KEY,
    mac         TEXT NOT NULL,
    camera_id   TEXT NOT NULL DEFAULT '',
    path        TEXT NOT NULL UNIQUE,
    started_at  TEXT NOT NULL,
    day         TEXT NOT NULL,
    hour        INTEGER NOT NULL,
    size_bytes  INTEGER NOT NULL,
    duration_s  INTEGER NOT NULL,
    indexed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recordings_mac_day ON recordings (mac, day);
"""

_MIGRATIONS = {
    "camera_id": "ALTER TABLE recordings ADD COLUMN camera_id TEXT NOT NULL DEFAULT ''",
}

# A segment is "finalized" once ffmpeg has moved on to the next one; we treat it as done
# when it hasn't been written to for this many seconds, then it is safe to index.
_FINALIZE_GRACE = 3.0

# --- runaway guards -----------------------------------------------------------------
# A remuxing ffmpeg (``-c:v copy`` + a 64 kbit AAC track) lives in the low tens of MB. Anything
# past this is a muxing-queue balloon, which — in a container without a memory cap — escalates
# into a *global* kernel OOM that kills host processes (it took down the WSL session's
# dbus-daemon). Kill and respawn long before that. See docker-compose.yml for the cgroup cap
# that contains the damage if this guard is somehow outrun.
_RSS_LIMIT_BYTES = 256 * 1024 * 1024
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")

# ffmpeg's stderr goes to a per-camera log opened in append mode. A stream that misbehaves can
# emit the same warning per packet, so cap the file and truncate in place (the O_APPEND writer
# just continues from the new end) instead of letting it grow to hundreds of MB.
_MAX_LOG_BYTES = 8 * 1024 * 1024

# Respawn backoff: a camera that is simply unreachable must not be retried in a tight loop.
_BACKOFF_BASE = 5.0
_BACKOFF_MAX = 120.0
_HEALTHY_AFTER = 60.0  # a process that ran at least this long counts as healthy: reset backoff

# FFmpeg's segment muxer cannot create directories. Keep a full day prepared so a transient
# maintenance failure cannot turn the next hour boundary into a recording outage. The maintenance
# loop refreshes this horizon every few seconds; retention deliberately preserves current/future
# empty directories (see retention.py).
_DIR_LOOKAHEAD_HOURS = 24


def _rss_bytes(pid: int) -> int | None:
    """Resident set size of a process, or ``None`` if it is gone / unreadable."""
    try:
        resident = Path(f"/proc/{pid}/statm").read_text().split()[1]
    except (OSError, IndexError, ValueError):
        return None
    return int(resident) * _PAGE_SIZE


def _safe_mac(mac: str) -> str:
    return mac.replace(":", "").lower()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(recordings)")}
        for column, ddl in _MIGRATIONS.items():
            if column not in existing:
                conn.execute(ddl)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recordings_camera_day "
            "ON recordings (camera_id, day)"
        )
        cameras_exist = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cameras'"
        ).fetchone()
        if cameras_exist:
            # Historical rows used a compact MAC as their archive key. Backfill only exact known
            # cameras; orphan footage remains queryable without inventing an identity.
            conn.execute(
                """UPDATE recordings
                   SET camera_id = COALESCE((
                       SELECT cameras.camera_id FROM cameras
                       WHERE replace(replace(lower(cameras.mac), ':', ''), '-', '') =
                             replace(replace(lower(recordings.mac), ':', ''), '-', '')
                   ), '')
                   WHERE camera_id = ''"""
            )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Recorder:
    """Runs and supervises the per-camera recording ffmpeg processes."""

    def __init__(self, segment_seconds: int | None = None, maint_interval: float = 5.0) -> None:
        settings = get_settings()
        self.root = Path(settings.recordings_dir)
        self.segment_seconds = segment_seconds or settings.segment_seconds
        self.maint_interval = maint_interval
        self._procs: dict[str, subprocess.Popen] = {}
        self._spawned_at: dict[str, float] = {}   # monotonic start time, for the health check
        self._fails: dict[str, int] = {}          # consecutive short-lived runs, drives backoff
        self._retry_at: dict[str, float] = {}     # monotonic time an ID may be respawned again
        # Process supervision and new archive paths are keyed by the driver-independent ID.
        # The indexer still recognizes historical MAC directories without moving their files.
        self._cameras: dict[str, registry.Camera] = {}
        self._paused = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._maint: threading.Thread | None = None

    # --- paths --------------------------------------------------------------------
    def _cam_dir(self, camera_id: str) -> Path:
        if not valid_camera_id(camera_id):
            raise ValueError("a valid opaque camera_id is required for recording output")
        return self.root / camera_id

    def _log_path(self, camera_id: str) -> Path:
        if not valid_camera_id(camera_id):
            raise ValueError("a valid opaque camera_id is required for recorder logs")
        return Path(get_settings().db_path).parent / f"rec_{camera_id}.log"

    def _output_template(self, camera_id: str) -> str:
        # strftime placeholders are expanded by ffmpeg's segment muxer.  _spawn gives that child
        # a fixed UTC timezone so this path cannot drift when the host/container/camera timezone
        # changes.  Camera packet timestamps are separately replaced with server wall-clock time.
        return str(self._cam_dir(camera_id) / "%Y-%m-%d" / "%H" / "%Y%m%d_%H%M%S.mp4")

    def _ensure_dirs(self, camera_ids: list[str]) -> None:
        # Must use the same clock basis as ffmpeg's UTC strftime expansion below.  Using naive
        # local time here can pre-create the wrong hour and make ffmpeg fail at a rollover.
        now = datetime.now(UTC)
        for offset in range(_DIR_LOOKAHEAD_HOURS + 1):
            when = now + timedelta(hours=offset)
            sub = Path(when.strftime("%Y-%m-%d")) / when.strftime("%H")
            for camera_id in camera_ids:
                (self._cam_dir(camera_id) / sub).mkdir(parents=True, exist_ok=True)

    # --- lifecycle ----------------------------------------------------------------
    def start(self, cameras: list[registry.Camera] | None = None) -> list[str]:
        """Start recording every camera with a usable stream. Return their opaque IDs."""
        init_db()
        if cameras is None:
            cameras = registry.list_cameras()
        new_cameras = {c.camera_id: c for c in cameras if c.rtsp_url and c.camera_id}
        camera_ids = list(new_cameras)
        self._paused = False
        self._ensure_dirs(camera_ids)
        with self._lock:
            self._cameras = new_cameras
            # Drop cameras no longer in the set (e.g. deleted via the API). A native MAC change
            # does not restart this process: both its go2rtc source and output path now use ID.
            for camera_id in [
                key for key in self._procs if key not in self._cameras
            ]:
                proc = self._procs[camera_id]
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                self._forget(camera_id)
            for camera_id in camera_ids:
                self._spawn_locked(camera_id)

        if self._maint is None or not self._maint.is_alive():
            self._stop.clear()
            self._maint = threading.Thread(target=self._maintain, daemon=True)
            self._maint.start()
        return camera_ids

    def pause(self) -> None:
        """Stop writing to disk (go2rtc keeps streaming); resumes via :meth:`resume`."""
        with self._lock:
            if self._paused:
                return
            self._paused = True
            self._terminate_all_locked()

    def resume(self) -> None:
        """Allow recording again; the maintenance loop respawns the ffmpeg processes."""
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def _retry_delay(self, camera_id: str) -> float:
        """Exponential backoff for a camera whose ffmpeg keeps dying young."""
        fails = self._fails.get(camera_id, 0)
        if fails <= 0:
            return 0.0
        return min(_BACKOFF_BASE * 2 ** (fails - 1), _BACKOFF_MAX)

    def _forget(self, camera_id: str) -> None:
        """Drop all supervision state for a camera (removed, paused, or stopped)."""
        self._procs.pop(camera_id, None)
        self._spawned_at.pop(camera_id, None)
        self._fails.pop(camera_id, None)
        self._retry_at.pop(camera_id, None)

    def _spawn_locked(self, camera_id: str) -> None:
        proc = self._procs.get(camera_id)
        if proc is not None:
            if proc.poll() is None:
                return  # already running
            # It exited on its own (stream drop, muxing-queue abort) or the watchdog killed it.
            # Only book the failure here, never respawn in the same pass, so a dead process is
            # counted exactly once.
            ran = time.monotonic() - self._spawned_at.pop(camera_id, 0.0)
            self._procs.pop(camera_id, None)
            self._fails[camera_id] = (
                0 if ran >= _HEALTHY_AFTER else self._fails.get(camera_id, 0) + 1
            )
            self._retry_at[camera_id] = time.monotonic() + self._retry_delay(camera_id)
            if self._fails[camera_id]:
                log.warning("recorder ffmpeg for %s exited after %.1fs (failure #%d); "
                            "retrying in %.0fs", camera_id, ran, self._fails[camera_id],
                            self._retry_delay(camera_id))
            return
        if time.monotonic() < self._retry_at.get(camera_id, 0.0):
            return  # still backing off
        self._procs[camera_id] = self._spawn(camera_id)
        self._spawned_at[camera_id] = time.monotonic()

    def _watchdog_locked(self) -> None:
        """Kill any ffmpeg whose memory has run away before it can OOM the host."""
        for camera_id, proc in list(self._procs.items()):
            if proc.poll() is not None:
                continue
            rss = _rss_bytes(proc.pid)
            if rss is None or rss <= _RSS_LIMIT_BYTES:
                continue
            log.error("recorder ffmpeg for %s ballooned to %d MB — killing it",
                      camera_id, rss // (1024 * 1024))
            proc.kill()
            try:
                proc.wait(timeout=5)  # reap it here; we drop the handle below
            except subprocess.TimeoutExpired:
                pass
            # Book it as a failure right here (the process is gone from _procs, so
            # _spawn_locked will not see the exit) so repeated balloons back off.
            self._procs.pop(camera_id, None)
            self._spawned_at.pop(camera_id, None)
            self._fails[camera_id] = self._fails.get(camera_id, 0) + 1
            self._retry_at[camera_id] = time.monotonic() + self._retry_delay(camera_id)

    def _trim_log(self, camera_id: str) -> None:
        """Truncate an oversized ffmpeg log in place (safe: the writer holds it O_APPEND)."""
        path = self._log_path(camera_id)
        try:
            if path.stat().st_size <= _MAX_LOG_BYTES:
                return
            with path.open("r+b") as fh:
                fh.truncate(0)
        except OSError:
            return
        log.info("truncated oversized recorder log %s", path.name)

    def _spawn(self, camera_id: str) -> subprocess.Popen:
        src = go2rtc.restream_rtsp_url(camera_id)
        self._trim_log(camera_id)
        logfile = self._log_path(camera_id).open("ab")
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            # --- input: repair timestamps and bound the demuxer -----------------------
            # The cameras deliver packets the RTSP demuxer cannot timestamp ("pts has no
            # value" / "Timestamps are unset in a packet"). With `-c:v copy` that propagates
            # straight to the mp4 muxer, which then cannot interleave video against the AAC
            # track and parks whole GOPs in its muxing queue — the queue grew to ~2 GB RSS and
            # triggered a global kernel OOM. Stamping every packet on arrival with the
            # wall clock gives both tracks one sane, monotonic time base, which is the actual
            # fix; the caps below are the belt-and-braces so a future oddity degrades into a
            # clean exit (the supervisor respawns) instead of eating the host's memory.
            "-fflags", "+genpts",
            "-use_wallclock_as_timestamps", "1",
            "-rtsp_transport", "tcp",
            "-rtbufsize", "32M", "-max_delay", "500000",
            "-i", src,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "64k",
            # The cameras timestamp audio with a wall-clock PTS; without this the audio track
            # starts tens of thousands of seconds in while the copied video is reset to 0, so
            # each segment reports a bogus multi-hour duration and the browser can't seek.
            # aresample first_pts=0 puts audio on the same zero base as the video, so
            # -reset_timestamps yields a correct per-segment duration (seekable clips).
            "-af", "aresample=async=1:first_pts=0",
            # Hard ceiling on the muxing queue, in packets *and* in bytes. Hitting it aborts
            # ffmpeg; a controlled death plus a respawn is strictly better than an OOM.
            "-max_muxing_queue_size", "1024",
            "-muxing_queue_data_threshold", str(32 * 1024 * 1024),
            "-avoid_negative_ts", "make_zero",
            "-f", "segment", "-segment_time", str(self.segment_seconds),
            "-segment_format", "mp4", "-reset_timestamps", "1", "-strftime", "1",
            # Write each segment as *fragmented* MP4: the moov index is written up front
            # (empty_moov) and the media is a chain of self-contained fragments flushed on
            # every keyframe. A hard kill (ffmpeg crash, WSL/host crash) then leaves the
            # in-progress segment playable up to its last flushed fragment instead of an
            # unindexed, unreadable file (a plain-mp4 segment only gets its trailing moov on
            # clean finalize, so an abrupt crash loses the whole current segment).
            "-segment_format_options",
            "movflags=+frag_keyframe+empty_moov+default_base_moof",
            self._output_template(camera_id),
        ]
        # FFmpeg's segment muxer has no per-output "strftime in UTC" flag; strftime follows the
        # process timezone.  Pin only the child to POSIX UTC0, which works both in and outside
        # Docker and does not depend on the image having a zoneinfo database installed.
        ffmpeg_env = os.environ.copy()
        ffmpeg_env["TZ"] = "UTC0"
        return subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=logfile, env=ffmpeg_env
        )

    def stop(self) -> None:
        self._stop.set()
        if self._maint is not None:
            self._maint.join(timeout=self.maint_interval + 2)
        with self._lock:
            self._terminate_all_locked()
        self._index(list_all=True)  # catch the final segments

    def _terminate_all_locked(self) -> None:
        for proc in self._procs.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in self._procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        # A deliberate stop is not a failure: clear the backoff so resume() restarts at once.
        # Covers IDs that are only *pending* a respawn too — those hold no entry in _procs.
        for camera_id in {*self._procs, *self._fails, *self._retry_at, *self._spawned_at}:
            self._forget(camera_id)

    def is_recording(self, camera_id: str) -> bool:
        proc = self._procs.get(camera_id)
        return proc is not None and proc.poll() is None

    # --- maintenance loop ---------------------------------------------------------
    def _maintain(self) -> None:
        while not self._stop.is_set():
            # These operations intentionally fail independently. Before this guard, one transient
            # indexing/SQLite/filesystem exception killed the only maintenance thread. FFmpeg then
            # kept writing until the first directory rollover, exited with ENOENT, and was never
            # supervised again until the whole container was restarted.
            camera_ids = list(self._cameras)
            try:
                self._ensure_dirs(camera_ids)
            except Exception:
                log.exception("recorder maintenance could not prepare output directories")
            if not self._paused:
                try:
                    with self._lock:  # reap runaways, then (re)spawn any crashed ffmpeg
                        self._watchdog_locked()
                        # Re-read under the lock: start() may have replaced the camera map while
                        # this maintenance pass was preparing legacy output directories.
                        for camera_id in self._cameras:
                            self._spawn_locked(camera_id)
                except Exception:
                    log.exception("recorder maintenance supervision pass failed")
            try:
                for camera_id in camera_ids:
                    self._trim_log(camera_id)
            except Exception:
                log.exception("recorder maintenance log pass failed")
            try:
                self._index()
            except Exception:
                log.exception("recorder maintenance indexing pass failed")
            self._stop.wait(self.maint_interval)

    def _index(self, list_all: bool = False) -> int:
        """Index finalized segments not yet recorded. Returns how many were added."""
        now = time.time()
        rows: list[tuple] = []
        with connect() as conn:
            camera_rows = conn.execute(
                "SELECT camera_id, mac FROM cameras"
            ).fetchall() if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cameras'"
            ).fetchone() else []
        identities: dict[str, tuple[str, str]] = {}
        for camera in camera_rows:
            safe_mac = _safe_mac(camera["mac"])
            identity = (camera["camera_id"], safe_mac)
            identities[camera["camera_id"]] = identity
            identities[safe_mac] = identity
        for cam_dir in self.root.iterdir() if self.root.exists() else []:
            if not cam_dir.is_dir():
                continue
            directory_key = cam_dir.name
            camera_id, mac = identities.get(directory_key, ("", directory_key))
            if valid_camera_id(directory_key):
                camera_id = directory_key
                mac = identities.get(directory_key, (directory_key, ""))[1]
            for path in cam_dir.rglob("*.mp4"):
                try:
                    st = path.stat()
                except OSError:
                    continue
                if not list_all and (now - st.st_mtime) < _FINALIZE_GRACE:
                    continue  # still being written
                try:
                    # Filenames produced by this recorder are UTC.  Keep the timezone marker in
                    # the index as well so downstream consumers never reinterpret the value as
                    # local time.  Legacy index rows remain untouched; historical filenames may
                    # have been produced before the UTC invariant and cannot be safely guessed.
                    started = datetime.strptime(path.stem, "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
                except ValueError:
                    continue
                rows.append((mac, camera_id, str(path), started.isoformat(timespec="seconds"),
                             started.strftime("%Y-%m-%d"), started.hour,
                             st.st_size, self.segment_seconds, _now()))
        if not rows:
            return 0
        with connect() as conn:
            # Upsert (not INSERT OR IGNORE): a fragmented-MP4 segment briefly exists as a tiny
            # header-only stub, so an early index pass can record a stub size; refresh size_bytes
            # once the file grows/finalizes. Only writes when the size actually changed (no churn
            # for finalized segments), so this also self-heals rows indexed before this fix.
            cur = conn.executemany(
                """INSERT INTO recordings
                   (mac, camera_id, path, started_at, day, hour, size_bytes, duration_s, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                       mac = CASE WHEN excluded.mac <> '' AND
                                           (excluded.camera_id <> '' OR recordings.camera_id = '')
                                  THEN excluded.mac ELSE recordings.mac END,
                       camera_id = CASE WHEN excluded.camera_id <> ''
                                        THEN excluded.camera_id ELSE recordings.camera_id END,
                       size_bytes = excluded.size_bytes,
                       indexed_at = excluded.indexed_at
                   WHERE recordings.size_bytes <> excluded.size_bytes
                      OR (excluded.mac <> ''
                          AND (excluded.camera_id <> '' OR recordings.camera_id = '')
                          AND recordings.mac <> excluded.mac)
                      OR (excluded.camera_id <> ''
                          AND recordings.camera_id <> excluded.camera_id)""",
                rows,
            )
            return cur.rowcount


def rekey_segments(old_mac: str, new_mac: str) -> int:
    """Update only the deprecated MAC projection after a native-identity correction.

    Opaque-ID archive paths and filters need no move. Historical MAC directories also stay where
    they are: their indexed absolute paths remain valid and ``camera_id`` already preserves camera
    ownership. Keeping this compatibility helper avoids destructive filesystem renames.
    """
    old, new = _safe_mac(old_mac), _safe_mac(new_mac)
    if old == new:
        return 0
    init_db()   # a scan can re-key before the recorder ever ran (autostart off / first boot)
    with connect() as conn:
        cameras_exist = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'cameras'"
        ).fetchone()
        camera = conn.execute(
            "SELECT camera_id FROM cameras WHERE mac = ?", (new_mac.lower(),)
        ).fetchone() if cameras_exist else None
        camera_id = str(camera["camera_id"]) if camera else ""
        cur = conn.execute(
            """UPDATE recordings SET mac = ?,
                   camera_id = CASE WHEN camera_id = '' THEN ? ELSE camera_id END
               WHERE mac = ?""",
            (new, camera_id, old),
        )
    log.info("updated deprecated MAC on %d recording rows %s -> %s", cur.rowcount, old, new)
    return cur.rowcount


MAX_PAGE = 200  # hard cap so one request can never pull a whole library


def query_segments(camera_id: str | None = None, mac: str | None = None,
                   day_from: str | None = None,
                   day_to: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """Paginated query over the recording index (for the recordings browser).

    Filters by opaque camera ID and an inclusive ``day_from``..``day_to`` range (``YYYY-MM-DD``).
    ``mac`` is a temporary compatibility filter for old clients and orphan historical rows.
    Newest first. ``limit`` is clamped to ``MAX_PAGE`` so a big library can never be pulled
    in one heavy response. Returns ``{items, total, limit, offset}``.
    """
    limit = max(1, min(int(limit or 50), MAX_PAGE))
    offset = max(0, int(offset or 0))
    where, params = [], []
    if camera_id:
        where.append("camera_id = ?"); params.append(camera_id)
    elif mac:
        where.append("mac = ?"); params.append(_safe_mac(mac))
    if day_from:
        where.append("day >= ?"); params.append(day_from)
    if day_to:
        where.append("day <= ?"); params.append(day_to)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM recordings" + clause, params).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM recordings" + clause + " ORDER BY started_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def segment_camera_id(path: str) -> str:
    """Return the indexed canonical owner of one segment, if known."""
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT camera_id FROM recordings WHERE path = ?", (str(path),)
        ).fetchone()
    return str(row["camera_id"]) if row and row["camera_id"] else ""
