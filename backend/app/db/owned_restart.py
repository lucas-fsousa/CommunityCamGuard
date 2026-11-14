"""One-use owned-RTSP restart receipts, saved only after physical ingress closure."""

from contextlib import closing
from dataclasses import dataclass

from ..camera_identity import valid_camera_id
from . import connect, source_policy

_SCHEMA = """CREATE TABLE IF NOT EXISTS media_owned_restart (
    camera_id TEXT PRIMARY KEY, revision INTEGER NOT NULL)"""


@dataclass(frozen=True)
class CleanRestart:
    camera_id: str
    revision: int


def _validate(item: CleanRestart) -> None:
    if (not valid_camera_id(item.camera_id) or type(item.revision) is not int
            or not 1 <= item.revision < source_policy.MAX_REVISION):
        raise ValueError("owned restart identity and revision required")


def _current(conn, item: CleanRestart) -> bool:
    row = conn.execute("SELECT * FROM media_source_policy WHERE camera_id=?", (item.camera_id,)).fetchone()
    return (row is not None and row['mode'] == 'owned_rtsp' and row['phase'] == 'blocked'
            and row['revision'] == item.revision
            and conn.execute("SELECT 1 FROM cameras WHERE camera_id=?", (item.camera_id,)).fetchone() is not None)


def save(items: tuple[CleanRestart, ...]) -> None:
    if not items:
        return
    if len({item.camera_id for item in items}) != len(items):
        raise ValueError("unique owned restart batch required")
    for item in items:
        _validate(item)
    with closing(connect()) as conn, conn:
        conn.execute(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        if not all(_current(conn, item) for item in items):
            raise source_policy.SourcePolicyConflict("owned retirement generation changed")
        conn.executemany("INSERT OR REPLACE INTO media_owned_restart VALUES (?,?)",
                         [(item.camera_id, item.revision) for item in items])


def pending() -> tuple[CleanRestart, ...]:
    with closing(connect()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='media_owned_restart'").fetchone() is None:
            return ()
        items = []
        for row in conn.execute("SELECT * FROM media_owned_restart").fetchall():
            item = CleanRestart(row['camera_id'], row['revision'])
            try:
                _validate(item)
            except ValueError:
                continue
            if _current(conn, item):
                items.append(item)
        return tuple(items)


def consume(item: CleanRestart) -> source_policy.Selection:
    _validate(item)
    with closing(connect()) as conn, conn:
        conn.execute(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT revision FROM media_owned_restart WHERE camera_id=?", (item.camera_id,)).fetchone()
        if row is None or row['revision'] != item.revision or not _current(conn, item):
            raise source_policy.SourcePolicyConflict("current clean owned retirement required")
        selected = source_policy.Selection('owned_rtsp', 'transition', item.revision + 1)
        conn.execute("UPDATE media_source_policy SET phase='transition',revision=? WHERE camera_id=?",
                     (selected.revision, item.camera_id))
        conn.execute("DELETE FROM media_owned_restart WHERE camera_id=?", (item.camera_id,))
        return selected
