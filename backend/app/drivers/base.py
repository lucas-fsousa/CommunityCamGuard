"""Camera driver framework — the single place to plug in a brand/model.

A **driver** bundles everything family-specific about a camera: the RTSP paths discovery
should try, how to recognise the family, and how to drive its controls (PTZ, reboot, ...).
Everything else — discovery, the capability probe, the API, the dashboard — is generic and
talks only to this interface. **Adding support for a new brand is: subclass
:class:`CameraDriver` in this package and register it in ``drivers/__init__.py``.** No engine
changes.

The low-level protocol toolboxes are reused by drivers rather than reimplemented:
- ONVIF SOAP ops → :mod:`..control.ptz` and :mod:`..control.device`
- RTSP client → :mod:`..discovery.rtsp`

A driver only wires those together for its family (and adds new toolboxes for non-ONVIF
brands). The generic :meth:`CameraDriver.probe` already handles the RTSP SDP (video/audio
tracks + codecs) that every RTSP camera shares; families override :meth:`_probe_controls`
to add what's specific (PTZ, reboot, model/firmware, ...).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ..discovery import rtsp
from .contracts import (
    AudioMessageResult,
    ControlDescriptor,
    ControlOption,
    ControlResult,
    ControlValue,
)

if TYPE_CHECKING:
    from ..db.registry import Camera
    from .onboarding import OnboardingPort


class Unsupported(Exception):
    """A driver was asked for a control its family doesn't support (PTZ, reboot, ...)."""


# The controllable features a driver may advertise — used for UI gating and docs.
FEATURES = ("ptz", "reboot", "audio_in", "audio_out", "led", "siren")

# How to label an open port when the dashboard surfaces it.
PORT_ROLES: dict[int, str] = {
    554: "rtsp",
    8554: "rtsp",
    80: "http",
    8000: "http",
    8080: "http",
    8899: "http-onvif",
    5000: "onvif-ptz",  # HiSilicon/Yoosee ONVIF service (PTZ + device) lives here
    50000: "p2p",  # vendor-app P2P channel (Gwell)
    34567: "proprietary-control",
    37777: "proprietary-control",
}


def classify_ports(open_ports: list[int]) -> dict[str, list[int]]:
    """Group open ports by role (``rtsp`` / ``http`` / ``onvif-ptz`` / ``p2p`` / ``unknown``)."""
    grouped: dict[str, list[int]] = {}
    for port in sorted(set(open_ports)):
        grouped.setdefault(PORT_ROLES.get(port, "unknown"), []).append(port)
    return grouped


@dataclass
class Capabilities:
    """A snapshot of what a camera supports. Serialised to JSON in the registry."""

    driver: str = "generic"  # which driver produced/owns this camera
    reachable: bool = False
    ptz: bool = False
    ptz_protocol: str = ""
    reboot: bool = False  # software reboot available (compliant cams)
    model: str = ""
    firmware: str = ""
    has_video: bool = False
    has_audio: bool = False
    video_codec: str = ""
    audio_codec: str = ""
    stream_paths: list[str] = field(
        default_factory=list
    )  # RTSP paths the camera itself reports (ONVIF)
    open_ports: list[int] = field(default_factory=list)
    ports_by_role: dict[str, list[int]] = field(default_factory=dict)
    probed_at: str = ""

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class DetectContext:
    """What discovery already knows about a host, used to pick the right driver."""

    vendor: str = ""  # SDP/device manufacturer string, if any
    model: str = ""
    firmware: str = ""
    serial: str = ""
    hardware: str = ""
    open_ports: list[int] = field(default_factory=list)
    sdp: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CameraDriver:
    """Base class for a camera family. Subclass, set the attributes, override what differs."""

    key: str = "generic"
    label: str = "Generic RTSP"
    rtsp_paths: tuple[str, ...] = ()  # ordered path templates ([USERNAME]/[PASSWORD]/[CHANNEL])
    transport: str = "auto"  # media-layer hint: auto | tcp | udp
    features: frozenset[str] = frozenset()  # advertised controllable features

    def onboarding(self) -> OnboardingPort | None:
        """Return this family onboarding port, when factory enrollment is implemented."""

        return None

    def matches(self, ctx: DetectContext) -> bool:
        """Does this camera belong to this family? The generic fallback never matches."""
        return False

    def match_confidence(self, ctx: DetectContext) -> int:
        """Return 0..100 confidence; explicit family evidence should outrank port fingerprints."""

        return 80 if self.matches(ctx) else 0

    # --- capability probe --------------------------------------------------------------
    def probe(self, camera: Camera, open_ports: list[int] | None = None) -> Capabilities:
        """Probe a camera and return its capabilities. Override :meth:`_probe_controls`, not this."""
        caps = Capabilities(
            driver=self.key, probed_at=_now(), open_ports=sorted(set(open_ports or []))
        )
        caps.ports_by_role = classify_ports(caps.open_ports)
        self._probe_rtsp(camera, caps)
        self._probe_controls(camera, caps)
        return caps

    def _probe_rtsp(self, camera: Camera, caps: Capabilities) -> None:
        """Generic, reusable: OPTIONS + one DESCRIBE to read the SDP media tracks."""
        ip = camera.last_ip
        port = camera.rtsp_port or 554
        if not ip:
            return
        try:
            session = rtsp.RtspSession(ip, port, 4.0, delay=0.3)
        except OSError:
            return
        try:
            opts = session.request("OPTIONS", f"rtsp://{ip}:{port}/")
            if opts is None or rtsp.parse_status(opts) == 0:
                return
            caps.reachable = True
            path = camera.stream_path or (self.rtsp_paths[0] if self.rtsp_paths else "/")
            uri = f"rtsp://{ip}:{port}{path}"
            resp = session.request("DESCRIBE", uri, accept_sdp=True)
            if resp is not None and rtsp.parse_status(resp) == 401 and camera.username:
                resp = session.request(
                    "DESCRIBE",
                    uri,
                    accept_sdp=True,
                    auth=rtsp.auth_header(resp, "DESCRIBE", uri, camera.username, camera.password),
                )
            if resp is not None and rtsp.parse_status(resp) == 200:
                sdp = rtsp.parse_sdp(resp)
                caps.has_video = bool(sdp["has_video"])
                caps.has_audio = bool(sdp["has_audio"])
                caps.video_codec = str(sdp["video_codec"])
                caps.audio_codec = str(sdp["audio_codec"])
        finally:
            session.close()

    def _probe_controls(self, camera: Camera, caps: Capabilities) -> None:
        """Hook: fill family-specific capabilities (PTZ, reboot, model...). Default: none."""

    def control_catalog(self, camera: Camera) -> tuple[ControlDescriptor, ...]:
        """Describe semantic controls available for this exact camera."""

        return ()

    def read_control(self, camera: Camera, key: str) -> ControlResult:
        """Read one allowlisted semantic control; family drivers opt in explicitly."""

        raise Unsupported(key)

    def control_options(self, camera: Camera, key: str) -> tuple[ControlOption, ...]:
        """Read runtime options for one explicitly advertised dynamic choice."""

        raise Unsupported(key)

    def write_control(self, camera: Camera, key: str, value: ControlValue) -> ControlResult:
        """Write one allowlisted semantic control; raw vendor payloads are never accepted."""

        raise Unsupported(key)

    def supports_audio_messages(self, camera: Camera) -> bool:
        """Whether this exact camera can receive bounded server-to-speaker PCM messages."""

        return False

    def send_audio_message(self, camera: Camera, pcm16le: bytes) -> AudioMessageResult:
        """Send canonical 16 kHz/mono/s16le PCM through a driver-owned transport."""

        raise Unsupported("audio_message")

    def supports_audio_streams(self, camera: Camera) -> bool:
        """Whether this camera can consume a bounded incremental PCM iterator."""

        return False

    def send_audio_stream(
        self, camera: Camera, pcm16le_chunks: Iterable[bytes]
    ) -> AudioMessageResult:
        """Consume one bounded 16 kHz/mono/s16le stream while retaining driver state."""

        raise Unsupported("audio_stream")

    # --- controls (default: unsupported) -----------------------------------------------
    def ptz(self, camera: Camera, direction: str | None, action: str = "step") -> bool:
        """Pan/tilt. ``action`` = ``start``/``stop`` (press-and-hold) or ``step`` (one nudge).

        ``direction`` is optional because a ``stop`` carries none; the movement helpers coerce a
        missing direction (see ``control/ptz.velocity_for``)."""
        raise Unsupported("ptz")

    def reboot(self, camera: Camera) -> bool:
        raise Unsupported("reboot")
