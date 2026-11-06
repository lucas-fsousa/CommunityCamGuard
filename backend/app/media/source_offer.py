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
    restart_domains: frozenset[str] = field(default_factory=frozenset, repr=False)

    def __post_init__(self) -> None:
        if not valid_camera_id(self.camera_id):
            raise ValueError("public camera ID required")
        if (type(self.domain) is not str or not 1 <= len(self.domain) <= 128
                or not self.domain.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in self.domain)
                or not callable(self.factory) or not callable(self.valid)):
            raise ValueError("trusted native source offer required")
        if (not isinstance(self.restart_domains, frozenset) or len(self.restart_domains) > 8
                or any(type(value) is not str or not 1 <= len(value) <= 128
                       or any(not 33 <= ord(c) <= 126 for c in value) for value in self.restart_domains)):
            raise ValueError("bounded driver-approved restart domains required")

    def accepts_restart_domain(self, domain: str) -> bool:
        return domain == self.domain or domain in self.restart_domains
