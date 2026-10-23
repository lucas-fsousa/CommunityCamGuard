"""Durable source intent, independent of camera brands, tokens and live endpoints.

Only an internal coordinator may change this policy after owning a handoff.
Changing intent does not retire a process or authorize a replacement source.
"""

from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass

from ..camera_identity import valid_camera_id
from . import connect

MODES = frozenset({"legacy_rtsp", "owned_rtsp", "native"})
PHASES = frozenset({"active", "transition", "blocked"})
MAX_REVISION = 2**63 - 1
_SCHEMA = """CREATE TABLE IF NOT EXISTS media_source_policy (
    camera_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    phase TEXT NOT NULL,
    revision INTEGER NOT NULL
)"""


class SourcePolicyConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class Selection:
    mode: str = "legacy_rtsp"
    phase: str = "active"
    revision: int = 0

    @property
    def permits_legacy_rtsp(self) -> bool:
        return self.mode == "legacy_rtsp" and self.phase == "active"


def _validate(camera_id: str) -> None:
    if type(camera_id) is not str or not valid_camera_id(camera_id):
        raise ValueError("public camera ID required")


def _selection(row) -> Selection:
    if row is None:
        return Selection()
    if (row["mode"] not in MODES or row["phase"] not in PHASES
            or type(row["revision"]) is not int or not 1 <= row["revision"] <= MAX_REVISION):
        # Corrupted state must never restore direct camera URLs by default.
        return Selection(phase="blocked", revision=-1)
    return Selection(row["mode"], row["phase"], row["revision"])


def init_db() -> None:
    with closing(connect()) as conn, conn:
        conn.execute(_SCHEMA)


@contextmanager
def snapshot(camera_ids: Iterable[str]) -> Iterator[dict[str, Selection]]:
    """Keep this read transaction through config write or consumer admission.

    A policy writer cannot commit while an old snapshot is still being applied.
    Missing pre-migration schema is read-only legacy compatibility, not an error.
    """
    ids = tuple(dict.fromkeys(camera_ids))
    for camera_id in ids:
        _validate(camera_id)
    if not ids:
        yield {}
        return
    with closing(connect()) as conn:
        conn.execute("BEGIN")
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_source_policy'",
        ).fetchone()
        selections = {}
        for camera_id in ids:
            row = conn.execute("SELECT * FROM media_source_policy WHERE camera_id=?",
                               (camera_id,)).fetchone() if exists else None
            selections[camera_id] = _selection(row)
        yield selections


def read_for(camera_ids: Iterable[str]) -> dict[str, Selection]:
    with snapshot(camera_ids) as selections:
        return selections


def _change(camera_id: str, expected: int, phase: str, mode: str | None = None) -> Selection:
    _validate(camera_id)
    if type(expected) is not int or not 0 <= expected < MAX_REVISION:
        raise ValueError("valid source revision required")
    with closing(connect()) as conn, conn:
        conn.execute(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM cameras WHERE camera_id=?", (camera_id,)).fetchone():
            raise ValueError("configured camera required")
        old = _selection(conn.execute("SELECT * FROM media_source_policy WHERE camera_id=?",
                                     (camera_id,)).fetchone())
        required = {"transition": {"active"}, "active": {"transition"},
                    "blocked": {"active", "transition"}}[phase]
        if old.revision != expected or old.phase not in required:
            raise SourcePolicyConflict("source policy revision or phase changed")
        result = Selection(mode or old.mode, phase, expected + 1)
        conn.execute("""INSERT INTO media_source_policy VALUES (?, ?, ?, ?)
                        ON CONFLICT(camera_id) DO UPDATE SET mode=excluded.mode,
                        phase=excluded.phase, revision=excluded.revision""",
                     (camera_id, result.mode, result.phase, result.revision))
        return result


def begin_change(camera_id: str, mode: str, *, expected_revision: int) -> Selection:
    if type(mode) is not str or mode not in MODES:
        raise ValueError("supported source mode required")
    return _change(camera_id, expected_revision, "transition", mode)


def activate(camera_id: str, *, expected_revision: int) -> Selection:
    """Coordinator only, after physical retirement and new-source preparation."""
    return _change(camera_id, expected_revision, "active")


def block(camera_id: str, *, expected_revision: int) -> Selection:
    return _change(camera_id, expected_revision, "blocked")
