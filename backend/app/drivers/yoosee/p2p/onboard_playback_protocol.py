"""Native-compatible protocol selection for Yoosee IoTVideo playback lists."""

from __future__ import annotations

from ...contracts import OnboardRecordingQuery

PLAYBACK_PROTOCOL_V1 = 1
PLAYBACK_PROTOCOL_V2 = 2
PLAYBACK_PROTOCOL_V3 = 3
PLAYBACK_PROTOCOL_V4 = 4

# ``_comm_req_list_handler`` compares ``(end_us - start_us) >> 35`` with 125.
_V3_WINDOW_THRESHOLD_US = 125 << 35


def select_onboard_playback_protocol(
    query: OnboardRecordingQuery,
    *,
    requested_version: int,
    minimum_version: int,
    ascending_order: bool = False,
    device_platform_version: int | None = None,
) -> int:
    """Mirror the SDK's fail-closed V2/V3/V4 selection rule.

    File/date operations pass a minimum of V2 and recording-type windows pass V3. The native SDK
    promotes a descending query spanning at least roughly 49.71 days from V1/V2 to V3. It then
    promotes V1-V3 to V4 only when its connection registry explicitly reports device platform 2.

    The broker inventory's ``new_platform`` bit is not treated as that platform enum: callers may
    supply ``device_platform_version`` only when an equivalent, independently proven value exists.
    """

    if not isinstance(query, OnboardRecordingQuery):
        raise ValueError("playback protocol query is invalid")
    if type(requested_version) is not int or requested_version not in range(1, 5):
        raise ValueError("requested playback protocol must be V1 through V4")
    if type(minimum_version) is not int or minimum_version not in range(1, 5):
        raise ValueError("minimum playback protocol must be V1 through V4")
    if type(ascending_order) is not bool:
        raise ValueError("playback ordering must be boolean")
    if device_platform_version is not None and (
        type(device_platform_version) is not int or device_platform_version < 0
    ):
        raise ValueError("device platform version is invalid")

    selected = max(requested_version, minimum_version)
    elapsed = query.end_utc - query.start_utc
    window_us = (
        elapsed.days * 86_400_000_000
        + elapsed.seconds * 1_000_000
        + elapsed.microseconds
    )
    if not ascending_order and window_us >= _V3_WINDOW_THRESHOLD_US and selected < 3:
        selected = 3
    if device_platform_version == 2 and selected <= 3:
        selected = 4
    return selected
