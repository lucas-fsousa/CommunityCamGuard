"""Server-owned recording destination and policy, independent of camera drivers."""

from dataclasses import dataclass
from pathlib import Path

from ..camera_identity import valid_camera_id
from .archive_names import ARCHIVE_TEMPLATE


def validate_segment_seconds(value: int) -> None:
    if type(value) is not int or not 60 <= value <= 2**31-1:
        raise ValueError("archive segment duration must be an integer of at least60 seconds")


@dataclass(frozen=True, slots=True)
class NativeArchiveSpec:
    camera_id: str
    root: Path
    segment_seconds: int

    def __post_init__(self) -> None:
        if not valid_camera_id(self.camera_id) or not isinstance(self.root, Path) or ":" in str(self.root):
            raise ValueError("local archive root and opaque camera identity required")
        validate_segment_seconds(self.segment_seconds)

    @property
    def template(self) -> Path:
        return self.root / self.camera_id / "%Y-%m-%d" / "%H" / ARCHIVE_TEMPLATE
