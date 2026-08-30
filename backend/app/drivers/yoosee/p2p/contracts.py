"""Stable internal contracts shared by Yoosee P2P protocol and feature adapters."""

from __future__ import annotations

from dataclasses import dataclass

# Every entry is queried with the read-only B7 family. Action roots describe capabilities here;
# they are never invoked with the AC action family by the generic property reader.
MODEL_READ_PATHS = frozenset(
    {
        "ProConst._productInfo",
        "ProConst._versionInfo",
        "ProConst.devFuncCfg",
        "ProConst.devFunCode",
        "ProReadonly._online",
        "ProReadonly.sysVer",
        "ProReadonly.connectInfo",
        "ProReadonly.power",
        "ProReadonly.simCard",
        "ProReadonly.devInfo",
        "ProReadonly.tfInfo",
        "ProReadonly.aiModeDownL",
        "ProWritable._almEvtSetting",
        "ProWritable._otaMode",
        "ProWritable.timeZone",
        "ProWritable.onvifEn",
        "ProWritable.recordParm",
        "ProWritable.guardParm",
        "ProWritable.videoParm",
        "ProWritable.csVideoRes",
        "ProWritable.nightViewModeV2",
        "ProWritable.motionZone",
        "ProWritable.workMode",
        "ProWritable.pressKeyCall",
        "ProWritable.screenSwitch",
        "ProWritable.antiFlickerSwitch",
        "ProWritable.volume",
        "ProWritable.indicatorLight",
        "ProWritable.audioMode",
        "ProWritable.whiteLightPlan",
        "ProWritable.autoWhiteLight",
        "ProWritable.autoWorkMode",
        "ProWritable.resFile",
        "ProWritable.whiteLightCtrl",
        "ProWritable.zoomFocusW",
        "Action.whiteLightCtrl",
        "Action.expelCtrl",
        "Action.laserCtrl",
        "Action.ptzCheck",
        "Action.zoomFocusA",
    }
)


class P2PProbeError(RuntimeError):
    """Sanitized P2P failure safe to expose through the authenticated local API."""


class InitInfoRejectedError(P2PProbeError):
    def __init__(self, error_code: int):
        self.error_code = error_code
        label = "stale session" if error_code == 0x216B else "access rejected"
        super().__init__(f"P2P access node rejected initialization: {label}")


@dataclass(frozen=True, slots=True)
class LoginMaterial:
    access_id: int
    access_token: bytes


@dataclass(frozen=True, slots=True)
class CertifiedNode:
    address: tuple[str, int]
    session_id: int
    session_key: bytes
    next_sequence: int


@dataclass(frozen=True, slots=True)
class OnlineDevice:
    device_id: int
    status: int
    new_platform: bool
    server_id: int
    terminal_id: bytes


@dataclass(frozen=True, slots=True)
class P2PInventory:
    device_id: str
    authenticated: bool
    device_count: int
    online_count: int
    target_visible: bool
    target_online: bool
    target_term_resolved: bool
    skipped_incomplete_nodes: int


@dataclass(frozen=True, slots=True)
class CallingAttempt:
    link_id: int
    call_id: int
    cookie: bytes


@dataclass(frozen=True, slots=True)
class CallingResult:
    node_acknowledged: bool
    node_notified: bool
    direct_datagrams: int
    direct_handshake: bool
    error_code: int | None
    peer_endpoint: tuple[str, int] | None
    next_sequence: int = 0
    route_link_id: int = 0
    attempt: CallingAttempt | None = None


@dataclass(frozen=True, slots=True)
class P2PRouteProbe:
    device_id: str
    authenticated: bool
    target_visible: bool
    target_online: bool
    broker_acknowledged: bool
    route_advertised: bool
    direct_datagrams: int
    direct_handshake: bool
    camera_contacted: bool
    broker_error_code: int | None


@dataclass(frozen=True, slots=True)
class ModelReadResult:
    transport_acknowledged: bool
    error_code: int | None
    value: object | None


@dataclass(frozen=True, slots=True)
class ModelWriteResult:
    transport_acknowledged: bool
    error_code: int | None


@dataclass(frozen=True, slots=True)
class P2PPropertyRead:
    device_id: str
    property_path: str
    authenticated: bool
    direct_handshake: bool
    transport_acknowledged: bool
    error_code: int | None
    value: object | None
