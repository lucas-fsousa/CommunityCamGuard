"""Vendor-neutral port for driver-owned factory onboarding operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class OnboardingAccountError(RuntimeError):
    """A driver's remote account operation failed."""


class OnboardingTransportError(RuntimeError):
    """A driver's privileged camera transport failed."""


@dataclass(frozen=True, slots=True)
class AccountLogin:
    account_type: str
    account: str
    password: str
    mobile_area: str = "0"
    language: str = "en"
    region: str = "US"
    area: str = "us"


@dataclass(frozen=True, slots=True)
class InventoryResult:
    authenticated: bool
    device_count: int
    online_count: int
    target_visible: bool
    target_online: bool
    target_term_resolved: bool
    skipped_incomplete_nodes: int


@dataclass(frozen=True, slots=True)
class RouteResult:
    authenticated: bool
    target_visible: bool
    target_online: bool
    broker_acknowledged: bool
    route_advertised: bool
    direct_datagrams: bool
    direct_handshake: bool
    camera_contacted: bool
    broker_error_code: int | None


@dataclass(frozen=True, slots=True)
class PropertyReadResult:
    property_path: str
    authenticated: bool
    direct_handshake: bool
    transport_acknowledged: bool
    error_code: int | None
    value: object


class OnboardingPort(Protocol):
    """Driver-owned operations needed by the generic onboarding HTTP workflow."""

    provider: str
    read_only_property_paths: frozenset[str]

    def init(self) -> None: ...

    def account_configured(self) -> bool: ...

    def login(self, request: AccountLogin) -> None: ...

    def refresh_account(self) -> None: ...

    def ble_material(
        self,
        device_id: str,
        *,
        fallback_file: Path | None,
        max_age_seconds: int,
    ) -> Any: ...

    def probe_inventory(self, device_id: str) -> InventoryResult: ...

    def probe_route(self, device_id: str) -> RouteResult: ...

    def read_property(self, device_id: str, property_path: str) -> PropertyReadResult: ...
