"""Internal experimental Yoosee AV invocation, never exposed as a public control.

Must run INSIDE the server process: a separate CLI would not share its operation
locks. The trusted operator supplies the reviewed test camera pair (camera 3).
No environment defaults, browser-supplied target, automatic worker or retry.
"""

from collections.abc import Callable
from dataclasses import dataclass

from ..db import p2p, registry
from ..drivers.yoosee.p2p.av_route import (
    AvRouteResult,
    PlatformRouteResult,
    probe_av_route,
    probe_platform_route,
)
from ..drivers.yoosee.p2p.av_sample import AvVideoSample
from ..drivers.yoosee.p2p.control_ownership import own_control_channel
from ..drivers.yoosee.p2p.tcp_budget import TcpProfile
from ..drivers.yoosee.p2p.tcp_route import TcpRouteResult, probe_tcp_route
from ..services.camera_controls import CameraNotFound, _exclusive
from .native_video_decode import DecodedVideo, decode_sample, require_decoder_tools


@dataclass(frozen=True, slots=True)
class DecodedAvResult:
    route: AvRouteResult | TcpRouteResult
    video: DecodedVideo


def run_reviewed_native_av(*, camera_id: str, reviewed_camera_id: str,
                           reviewed_device_id: str,
                           decode_video: bool = False,
                           platform_only: bool = False,
                           tcp_relay: bool = False,
                           tcp_profile: str = "short",
                           cancelled: Callable[[], bool] = lambda: False
                           ) -> AvRouteResult | DecodedAvResult | PlatformRouteResult | TcpRouteResult:
    """One reviewed finite experiment under the existing PTZ/audio/control lock.

    The two reviewed IDs must come from trusted internal configuration, not HTTP.
    Also reserves the driver's shared broker against in-process warmup/logins.
    This does not lock RTSP or other processes.
    """
    if not reviewed_camera_id or camera_id != reviewed_camera_id or not reviewed_device_id:
        raise ValueError("native AV diagnostic target is not the reviewed test camera")
    if platform_only and decode_video:
        raise ValueError("platform-only diagnostic cannot decode video")
    if platform_only and tcp_relay:
        raise ValueError("platform-only diagnostic cannot open TCP media")
    if tcp_profile not in {"short", "extended"}:
        raise ValueError("unreviewed TCP experiment profile")
    profile = TcpProfile.SHORT if tcp_profile == "short" else TcpProfile.EXTENDED
    if profile is TcpProfile.EXTENDED and (not tcp_relay or decode_video or platform_only):
        raise ValueError("extended diagnostic requires TCP without decoding/platform-only")
    with _exclusive(camera_id), own_control_channel():
        camera = registry.get_camera_by_id(camera_id)
        if camera is None:
            raise CameraNotFound("camera not found")
        enrollment = p2p.get_enrollment_for_camera(camera_id)
        if (enrollment is None or enrollment.camera_id != camera_id
                or enrollment.device_id != reviewed_device_id):
            raise ValueError("native AV diagnostic enrollment mismatch")
        if platform_only:
            return probe_platform_route(enrollment, camera_id=camera_id,
                                        device_id=reviewed_device_id, cancelled=cancelled)
        if not decode_video:
            if tcp_relay:
                return probe_tcp_route(enrollment, camera_id=camera_id,
                                       device_id=reviewed_device_id, cancelled=cancelled, profile=profile)
            return probe_av_route(enrollment, camera_id=camera_id, device_id=reviewed_device_id,
                                  duration=3.0, cancelled=cancelled)
        require_decoder_tools()  # Fail before touching a camera if tools are unavailable.
        sample = AvVideoSample()
        try:
            route: AvRouteResult | TcpRouteResult
            if tcp_relay:
                route = probe_tcp_route(enrollment, camera_id=camera_id,
                                        device_id=reviewed_device_id, cancelled=cancelled, sample=sample,
                                        profile=profile)
            else:
                route = probe_av_route(enrollment, camera_id=camera_id, device_id=reviewed_device_id,
                                       duration=3.0, cancelled=cancelled, sample=sample)
            # Physical sockets are closed; broker receipt remains a separate result.
            return DecodedAvResult(route, decode_sample(sample, cancelled=cancelled))
        finally:
            sample.close()
