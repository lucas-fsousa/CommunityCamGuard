"""Yoosee adapter for the generic driver onboarding port."""

from __future__ import annotations

from pathlib import Path

from ...provisioning import OnboardingCompletionError as NativeCompletionError
from ...provisioning import (
    complete_camera_onboarding,
    locate_camera_by_mac,
)
from ..onboarding import (
    AccountLogin,
    BleDecodeResult,
    BlePreparation,
    CompletionMediaProof,
    CompletionResult,
    InventoryResult,
    OnboardingAccountError,
    OnboardingCompletionError,
    OnboardingInputError,
    OnboardingLabelError,
    OnboardingStateError,
    OnboardingTransportError,
    OnlineStatusResult,
    PropertyReadResult,
    RouteResult,
)
from . import account_store
from .ble import (
    BleCodecError,
    begin_ble_provisioning_attempt,
    ble_provisioning_attempt,
    build_ble_provisioning_frames,
    load_ble_provisioning_material,
)
from .ble_onboarding import decode_response
from .labels import LabelError, inspect_label
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
from .privileged import (
    PrivilegedEnrollmentError,
    bind_vendor_device,
    bound_privileged_enrollment,
    mark_privileged_enrollment_bound,
    pending_privileged_enrollment,
    privileged_enrollment_status,
    query_vendor_device_online,
    remember_privileged_handoff,
)
from .qr import build_wifi_payload, encryption_from_scan, render_svg_base64
from .vendor_cloud import VendorProvisioningCloudError, fetch_native_ble_material


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

    def build_wifi_qr(self, *, ssid: str, password: str, security: str) -> str:
        payload = build_wifi_payload(
            ssid=ssid,
            password=password,
            encryption=encryption_from_scan(security, password),
        )
        return render_svg_base64(payload)

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

    def prepare_ble(
        self,
        *,
        device_id: str,
        ssid: str,
        password: str,
        security: str,
        fallback_file: Path | None,
        max_age_seconds: int,
    ) -> BlePreparation:
        try:
            material = self.ble_material(
                device_id,
                fallback_file=fallback_file,
                max_age_seconds=max_age_seconds,
            )
            wifi_payload = build_wifi_payload(
                ssid=ssid,
                password=password,
                encryption=encryption_from_scan(security, password),
                user_id=material.server_user_id,
                config_token=material.config_token,
            )
            frames = build_ble_provisioning_frames(material, wifi_payload=wifi_payload, mtu=256)
            attempt = begin_ble_provisioning_attempt(material)
        except (BleCodecError, ValueError) as exc:
            raise OnboardingInputError(str(exc)) from exc
        except VendorProvisioningCloudError as exc:
            raise OnboardingTransportError(str(exc)) from exc
        return BlePreparation(
            attempt_id=attempt.attempt_id,
            expires_at=attempt.expires_at,
            frames=frames,
        )

    def decode_ble(
        self,
        *,
        device_id: str,
        attempt_id: str,
        command: int,
        encrypted: bool,
        raw: bytes,
    ) -> BleDecodeResult:
        return decode_response(
            device_id=device_id,
            attempt_id=attempt_id,
            command=command,
            encrypted=encrypted,
            raw=raw,
        )

    def privileged_status(self, device_id: str) -> dict:
        return privileged_enrollment_status(device_id)

    def online_status(self, *, device_id: str, attempt_id: str) -> OnlineStatusResult:
        try:
            attempt = ble_provisioning_attempt(attempt_id, expected_device_id=device_id)
            result = query_vendor_device_online(attempt.material)
            if result.device_id is not None and result.device_id != device_id:
                raise OnboardingStateError("vendor online result belongs to a different camera")
            handoff_ready = False
            if result.online:
                remember_privileged_handoff(attempt.material, confirm_key=None)
                handoff_ready = True
        except (BleCodecError, PrivilegedEnrollmentError) as exc:
            raise OnboardingStateError(str(exc)) from exc
        return OnlineStatusResult(
            query_succeeded=result.success,
            online=result.online,
            terminal_failure=result.terminal_failure,
            code=result.code,
            handoff_ready=handoff_ready,
        )

    def bind(
        self,
        *,
        device_id: str,
        time_area: str,
        time_zone: int,
        camera_id: str | None,
    ) -> None:
        try:
            pending = pending_privileged_enrollment(device_id)
            result = bind_vendor_device(
                pending,
                time_area=time_area,
                time_zone=time_zone,
            )
            if not result.success:
                detail = result.message or (
                    str(result.code) if result.code is not None else "unknown error"
                )
                raise OnboardingStateError(f"camera P2P enrollment failed: {detail}")
            if not result.dev_token:
                raise OnboardingTransportError(
                    "camera P2P enrollment returned no subscription material"
                )
            mark_privileged_enrollment_bound(
                pending,
                result.dev_token,
                camera_id=camera_id,
            )
        except PrivilegedEnrollmentError as exc:
            raise OnboardingStateError(str(exc)) from exc

    def probe_inventory(self, device_id: str) -> InventoryResult:
        try:
            enrollment = bound_privileged_enrollment(device_id)
            result = probe_account_inventory(enrollment)
        except PrivilegedEnrollmentError as exc:
            raise OnboardingStateError(str(exc)) from exc
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
        try:
            enrollment = bound_privileged_enrollment(device_id)
            result = probe_camera_route(enrollment)
        except PrivilegedEnrollmentError as exc:
            raise OnboardingStateError(str(exc)) from exc
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
        try:
            enrollment = bound_privileged_enrollment(device_id)
            result = read_camera_property(enrollment, property_path)
        except PrivilegedEnrollmentError as exc:
            raise OnboardingStateError(str(exc)) from exc
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
