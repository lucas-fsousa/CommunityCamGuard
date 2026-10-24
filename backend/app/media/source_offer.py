"""Trusted driver offer: lazy source construction and local evidence revalidation."""

from collections.abc import Callable
from dataclasses import dataclass, field

from ..camera_identity import valid_camera_id
from .source_registry import ManagedSource


@dataclass(frozen=True)
class NativeSourceOffer:
    camera_id: str
    domain: str = field(repr=False)
    factory: Callable[[], ManagedSource] = field(repr=False)
    valid: Callable[[], bool] = field(repr=False)

    def __post_init__(self) -> None:
        if not valid_camera_id(self.camera_id):
            raise ValueError("public camera ID required")
        if (type(self.domain) is not str or not 1 <= len(self.domain) <= 128
                or not self.domain.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in self.domain)
                or not callable(self.factory) or not callable(self.valid)):
            raise ValueError("trusted native source offer required")
