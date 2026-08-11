"""Factory-new camera onboarding primitives."""

from .labels import LabelError, inspect_label
from .wifi import WifiSelectionError, scan_wifi_networks, selected_ssid

__all__ = [
    "LabelError",
    "WifiSelectionError",
    "inspect_label",
    "scan_wifi_networks",
    "selected_ssid",
]
