"""Factory-new camera onboarding primitives."""

from .rtsp_completion import (
    CompletedCamera,
    LocatedCamera,
    OnboardingCompletionError,
    RtspMediaProof,
    complete_camera_onboarding,
    locate_camera_by_mac,
    prove_rtsp_media,
)
from .wifi import WifiSelectionError, manual_network, scan_wifi_networks, selected_network

__all__ = [
    "CompletedCamera",
    "LocatedCamera",
    "OnboardingCompletionError",
    "RtspMediaProof",
    "WifiSelectionError",
    "complete_camera_onboarding",
    "locate_camera_by_mac",
    "manual_network",
    "prove_rtsp_media",
    "scan_wifi_networks",
    "selected_network",
]
