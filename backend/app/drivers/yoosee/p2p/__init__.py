"""Bounded native client for the Yoosee/Gwell IoTVideo P2P control plane."""

from .account import (
    AccountCredentials,
    AccountSession,
    VendorAccountError,
    login_account,
    refresh_account_session,
)
from .client import (
    probe_account_inventory,
    probe_camera_route,
    read_camera_property,
)
from .contracts import (
    MODEL_READ_PATHS,
    P2PInventory,
    P2PProbeError,
    P2PPropertyRead,
    P2PRouteProbe,
)
from .orientation import ORIENTATION_VALUES, P2POrientationWrite, set_camera_orientation
from .renewal import run_with_fresh_access
from .rtsp_setup import (
    P2PRtspEnableWrite,
    P2PRtspPreparation,
    generate_rtsp_password,
    prepare_camera_rtsp,
    rtsp_password_digest,
    set_camera_rtsp_enabled,
)
from .siren import P2PSirenPulse, pulse_camera_siren
from .white_light import (
    P2PWhiteLightState,
    P2PWhiteLightWrite,
    read_camera_white_light,
    set_camera_white_light,
)

__all__ = [
    "MODEL_READ_PATHS",
    "ORIENTATION_VALUES",
    "AccountCredentials",
    "AccountSession",
    "P2PInventory",
    "P2POrientationWrite",
    "P2PProbeError",
    "P2PPropertyRead",
    "P2PRouteProbe",
    "P2PRtspEnableWrite",
    "P2PRtspPreparation",
    "P2PSirenPulse",
    "P2PWhiteLightState",
    "P2PWhiteLightWrite",
    "VendorAccountError",
    "generate_rtsp_password",
    "login_account",
    "prepare_camera_rtsp",
    "probe_account_inventory",
    "probe_camera_route",
    "pulse_camera_siren",
    "read_camera_property",
    "read_camera_white_light",
    "refresh_account_session",
    "rtsp_password_digest",
    "run_with_fresh_access",
    "set_camera_orientation",
    "set_camera_rtsp_enabled",
    "set_camera_white_light",
]
