"""Vendor-neutral port for driver-owned factory onboarding operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class OnboardingAccountError(RuntimeError):
    """A driver's remote account operation failed."""


class OnboardingLabelError(ValueError):
    """A printed/scanned identity is not valid for the selected camera family."""


class OnboardingInputError(ValueError):
    """A driver-specific provisioning input cannot be encoded safely."""


class OnboardingTransportError(RuntimeError):
    """A driver's privileged camera transport failed."""


class OnboardingStateError(RuntimeError):
    """Required durable or ephemeral onboarding state is unavailable."""


class OnboardingCompletionError(RuntimeError):
    """A stage-aware failure while completing a camera into the generic registry."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(message)


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


@dataclass(frozen=True, slots=True)
class CompletionMediaProof:
    transport: str
    has_video: bool
    has_audio: bool
    video_codec: str
    audio_codec: str


@dataclass(frozen=True, slots=True)
class CompletionResult:
    camera: Any
    proof: CompletionMediaProof
    stages: tuple[str, ...]
    already_configured: bool


@dataclass(frozen=True, slots=True)
class BlePreparation:
    attempt_id: str
    expires_at: float
    frames: dict[str, list[bytes]]


class OnboardingPort(Protocol):
    """Driver-owned operations needed by the generic onboarding HTTP workflow."""

    provider: str
    read_only_property_paths: frozenset[str]

    def init(self) -> None: ...

    def account_configured(self) -> bool: ...

    def inspect_label(
        self,
        *,
        label: str,
        device_id: str,
        capability_code: str,
        firmware_version: str,
        mac: str,
    ) -> dict: ...

    def build_wifi_qr(self, *, ssid: str, password: str, security: str) -> str: ...

    def prepare_ble(
        self,
        *,
        device_id: str,
        ssid: str,
        password: str,
        security: str,
        fallback_file: Path | None,
        max_age_seconds: int,
    ) -> BlePreparation: ...

    def login(self, request: AccountLogin) -> None: ...

    def refresh_account(self) -> None: ...

    def probe_inventory(self, device_id: str) -> InventoryResult: ...

    def probe_route(self, device_id: str) -> RouteResult: ...

    def read_property(self, device_id: str, property_path: str) -> PropertyReadResult: ...

    def complete(
        self,
        *,
        device_id: str,
        mac: str,
        name: str,
        firmware_hint: str,
    ) -> CompletionResult: ...
