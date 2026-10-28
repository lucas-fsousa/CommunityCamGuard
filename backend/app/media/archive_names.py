"""UTC archive names with optional decoder-epoch isolation, independent of drivers."""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

ARCHIVE_TEMPLATE = "%Y%m%d_%H%M%S.mp4"
_NAME = re.compile(r"[0-9]{8}_[0-9]{6}(?:_e[0-9a-f]{32})?\.mp4", re.ASCII)


def epoch_template(template: Path) -> Path:
    """New basename per decoder; keep existing UTC day/hour directories unchanged."""
    if template.name != ARCHIVE_TEMPLATE:
        raise ValueError("timestamped UTC archive template required")
    return template.with_name(f"%Y%m%d_%H%M%S_e{uuid.uuid4().hex}.mp4")


def started_at(path: Path) -> datetime:
    if _NAME.fullmatch(path.name) is None:
        raise ValueError("unsupported recording filename")
    return datetime.strptime(path.name[:15], "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
