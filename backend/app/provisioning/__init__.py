"""Factory-new camera onboarding primitives."""

from .privileged import (
    PrivilegedEnrollmentError,
    bind_vendor_device,
    bound_privileged_enrollment,
    bound_privileged_enrollment_for_camera,
    mark_privileged_enrollment_bound,
    pending_privileged_enrollment,
    privileged_enrollment_status,
    query_vendor_device_online,
    remember_privileged_handoff,
)
from .rtsp_completion import (
    CompletedCamera,
    LocatedCamera,
    OnboardingCompletionError,
    RtspMediaProof,
    complete_camera_onboarding,
    locate_camera_by_mac,
    prove_rtsp_media,
)
from .vendor_cloud import VendorProvisioningCloudError, fetch_native_ble_material
from .wifi import WifiSelectionError, manual_network, scan_wifi_networks, selected_network

__all__ = [
    "CompletedCamera",
    "LocatedCamera",
    "OnboardingCompletionError",
    "PrivilegedEnrollmentError",
    "RtspMediaProof",
    "VendorProvisioningCloudError",
    "WifiSelectionError",
    "bind_vendor_device",
    "bound_privileged_enrollment",
    "bound_privileged_enrollment_for_camera",
    "complete_camera_onboarding",
    "fetch_native_ble_material",
    "locate_camera_by_mac",
    "manual_network",
    "mark_privileged_enrollment_bound",
    "pending_privileged_enrollment",
    "privileged_enrollment_status",
    "prove_rtsp_media",
    "query_vendor_device_online",
    "remember_privileged_handoff",
    "scan_wifi_networks",
    "selected_network",
]
