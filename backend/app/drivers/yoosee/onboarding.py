"""Yoosee adapter for the generic driver onboarding port."""

from __future__ import annotations

from pathlib import Path

from ...provisioning import (
    LabelError,
    PrivilegedEnrollmentError,
    bound_privileged_enrollment,
    complete_camera_onboarding,
    fetch_native_ble_material,
    inspect_label,
    load_ble_provisioning_material,
    locate_camera_by_mac,
)
from ...provisioning import OnboardingCompletionError as NativeCompletionError
from ..onboarding import (
    AccountLogin,
    CompletionMediaProof,
    CompletionResult,
    InventoryResult,
    OnboardingAccountError,
    OnboardingCompletionError,
    OnboardingLabelError,
    OnboardingStateError,
    OnboardingTransportError,
    PropertyReadResult,
    RouteResult,
)
from . import account_store
from .p2p import (
    MODEL_READ_PATHS,
    AccountCredentials,
    P2PProbeError,
    VendorAccountError,
    login_account,
    probe_account_inventory,
    probe_camera_route,
    read_camera_property,
    refresh_account_session,
)


class YooseeOnboarding:
    provider = account_store.PROVIDER
    read_only_property_paths = frozenset(MODEL_READ_PATHS)

    def init(self) -> None:
        account_store.init_db()

    def account_configured(self) -> bool:
        return account_store.get_account() is not None

    def inspect_label(
        self,
        *,
        label: str,
        device_id: str,
        capability_code: str,
        firmware_version: str,
        mac: str,
    ) -> dict:
        try:
            return inspect_label(
                label=label,
                device_id=device_id,
                capability_code=capability_code,
                firmware_version=firmware_version,
                mac=mac,
            )
        except LabelError as exc:
            raise OnboardingLabelError(str(exc)) from exc

    def login(self, request: AccountLogin) -> None:
        try:
            credentials = AccountCredentials.from_password(
                account_type=request.account_type,
                account=request.account,
                password=request.password,
                mobile_area=request.mobile_area,
                language=request.language,
                region=request.region,
                area=request.area,
            )
            session = login_account(credentials)
            account_store.save_account(credentials, session)
        except VendorAccountError as exc:
            raise OnboardingAccountError(str(exc)) from exc

    def refresh_account(self) -> None:
        stored = account_store.get_account()
        if stored is None:
            raise LookupError("vendor account is not configured")
        try:
            refreshed = refresh_account_session(stored.session)
            account_store.update_session(refreshed)
        except VendorAccountError as exc:
            raise OnboardingAccountError(str(exc)) from exc

    def ble_material(
        self,
        device_id: str,
        *,
        fallback_file: Path | None,
        max_age_seconds: int,
    ):
        stored = account_store.get_account()
        try:
            if stored is not None:
                refreshed = refresh_account_session(stored.session)
                account_store.update_session(refreshed)
                return fetch_native_ble_material(refreshed, device_id=device_id)
            if fallback_file is not None:
                return load_ble_provisioning_material(
                    fallback_file,
                    expected_device_id=device_id,
                    max_age_seconds=max_age_seconds,
                )
        except VendorAccountError as exc:
            raise OnboardingAccountError(str(exc)) from exc
        raise LookupError("BLE handshake material is unavailable; configure the vendor account")

    def probe_inventory(self, device_id: str) -> InventoryResult:
        enrollment = bound_privileged_enrollment(device_id)
        try:
            result = probe_account_inventory(enrollment)
        except P2PProbeError as exc:
            raise OnboardingTransportError(str(exc)) from exc
        return InventoryResult(
            authenticated=result.authenticated,
            device_count=result.device_count,
            online_count=result.online_count,
            target_visible=result.target_visible,
            target_online=result.target_online,
            target_term_resolved=result.target_term_resolved,
            skipped_incomplete_nodes=result.skipped_incomplete_nodes,
        )

    def probe_route(self, device_id: str) -> RouteResult:
        enrollment = bound_privileged_enrollment(device_id)
        try:
            result = probe_camera_route(enrollment)
        except P2PProbeError as exc:
            raise OnboardingTransportError(str(exc)) from exc
        return RouteResult(
            authenticated=result.authenticated,
            target_visible=result.target_visible,
            target_online=result.target_online,
            broker_acknowledged=result.broker_acknowledged,
            route_advertised=result.route_advertised,
            direct_datagrams=result.direct_datagrams,
            direct_handshake=result.direct_handshake,
            camera_contacted=result.camera_contacted,
            broker_error_code=result.broker_error_code,
        )

    def read_property(self, device_id: str, property_path: str) -> PropertyReadResult:
        enrollment = bound_privileged_enrollment(device_id)
        try:
            result = read_camera_property(enrollment, property_path)
        except P2PProbeError as exc:
            raise OnboardingTransportError(str(exc)) from exc
        return PropertyReadResult(
            property_path=result.property_path,
            authenticated=result.authenticated,
            direct_handshake=result.direct_handshake,
            transport_acknowledged=result.transport_acknowledged,
            error_code=result.error_code,
            value=result.value,
        )

    def complete(
        self,
        *,
        device_id: str,
        mac: str,
        name: str,
        firmware_hint: str,
    ) -> CompletionResult:
        try:
            enrollment = bound_privileged_enrollment(device_id)
            located = locate_camera_by_mac(mac)
            completed = complete_camera_onboarding(
                enrollment,
                located,
                device_id=device_id,
                name=name,
                firmware_hint=firmware_hint,
            )
        except PrivilegedEnrollmentError as exc:
            raise OnboardingStateError(str(exc)) from exc
        except NativeCompletionError as exc:
            raise OnboardingCompletionError(exc.stage, str(exc)) from exc
        proof = completed.proof
        return CompletionResult(
            camera=completed.camera,
            proof=CompletionMediaProof(
                transport=proof.transport,
                has_video=proof.has_video,
                has_audio=proof.has_audio,
                video_codec=proof.video_codec,
                audio_codec=proof.audio_codec,
            ),
            stages=tuple(completed.stages),
            already_configured=completed.already_configured,
        )


ONBOARDING = YooseeOnboarding()
