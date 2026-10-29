"""One-use clean native retirement checkpoints, never capabilities or credentials.

Only the native runtime writes these after all its owners/threads confirm exit.
Persisted source intent alone is insufficient to issue or consume a checkpoint.
"""

from contextlib import closing
from dataclasses import dataclass, field

from ..camera_identity import valid_camera_id
from . import connect, source_policy

_SCHEMA = """CREATE TABLE IF NOT EXISTS media_native_restart (
    camera_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    domain TEXT NOT NULL,
    version INTEGER NOT NULL
)"""


@dataclass(frozen=True)
class CleanRestart:
    camera_id: str
    revision: int
    domain: str = field(repr=False)


def _validate(item: CleanRestart) -> None:
    if (not valid_camera_id(item.camera_id) or type(item.revision) is not int
            or not 1 <= item.revision < source_policy.MAX_REVISION
            or type(item.domain) is not str or not 1 <= len(item.domain) <= 128
            or any(not 33 <= ord(c) <= 126 for c in item.domain)):
        raise ValueError("valid native checkpoint identity/revision/domain required")


def _current(conn, item: CleanRestart) -> bool:
    row = conn.execute("SELECT * FROM media_source_policy WHERE camera_id=?",(item.camera_id,)).fetchone()
    return (row is not None and row['mode']=='native' and row['phase']=='blocked'
            and row['revision']==item.revision
            and conn.execute("SELECT 1 FROM cameras WHERE camera_id=?",(item.camera_id,)).fetchone() is not None)


def save(items: tuple[CleanRestart, ...]) -> None:
    """Runtime-only, after confirmed closure of every native owner/executor."""
    if not items:
        return
    if len(items)>32 or len({item.camera_id for item in items})!=len(items):
        raise ValueError("bounded unique checkpoint batch required")
    for item in items: _validate(item)
    with closing(connect()) as conn, conn:
        conn.execute(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        if not all(_current(conn,item) for item in items):
            raise source_policy.SourcePolicyConflict("native retirement generation changed")
        conn.executemany("INSERT OR REPLACE INTO media_native_restart VALUES (?,?,?,1)",
                         [(item.camera_id,item.revision,item.domain) for item in items])


def pending() -> tuple[CleanRestart, ...]:
    """Bounded read, no schema creation or attempt to connect to a camera."""
    with closing(connect()) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_native_restart'").fetchone() is None:
            return ()
        rows=conn.execute("SELECT * FROM media_native_restart LIMIT 33").fetchall()
        if len(rows)>32:
            raise source_policy.SourcePolicyConflict("native restart capacity exceeded")
        items=[]
        for row in rows:
            item=CleanRestart(row['camera_id'],row['revision'],row['domain'])
            try: _validate(item)
            except ValueError: continue
            if row['version']==1 and _current(conn,item): items.append(item)
        return tuple(items)


def discard(camera_id: str) -> None:
    """Invalidate before any new worker birth, including unsuccessful activations."""
    if not valid_camera_id(camera_id): raise ValueError("opaque camera identity required")
    with closing(connect()) as conn, conn:
        conn.execute(_SCHEMA)
        conn.execute("DELETE FROM media_native_restart WHERE camera_id=?",(camera_id,))


def consume(item: CleanRestart) -> source_policy.Selection:
    """Atomically spend the checkpoint and advance the exact blocked native intent."""
    _validate(item)
    with closing(connect()) as conn, conn:
        conn.execute(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        row=conn.execute("SELECT * FROM media_native_restart WHERE camera_id=?",(item.camera_id,)).fetchone()
        if (row is None or row['version']!=1 or row['revision']!=item.revision
                or row['domain']!=item.domain or not _current(conn,item)):
            raise source_policy.SourcePolicyConflict("current clean native retirement required")
        selected=source_policy.Selection('native','transition',item.revision+1)
        conn.execute("UPDATE media_source_policy SET phase='transition',revision=? WHERE camera_id=?",
                     (selected.revision,item.camera_id))
        conn.execute("DELETE FROM media_native_restart WHERE camera_id=?",(item.camera_id,))
        return selected
