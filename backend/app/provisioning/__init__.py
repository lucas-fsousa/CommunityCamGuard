"""Factory-new camera onboarding primitives."""

from .wifi import WifiSelectionError, manual_network, scan_wifi_networks, selected_network

__all__ = [
    "WifiSelectionError",
    "manual_network",
    "scan_wifi_networks",
    "selected_network",
]
