"""Pure SDK message bodies for Yoosee onboard-playback queries.

These builders deliberately stop at the eight-byte ``BuiltInCmd`` message
boundary accepted by ``MessageMgr::send_msg_to_device``.  They do not select
or open a transport.  The current SDK eventually carries this message in B9,
but only through its route-aware passthrough finalizer; callers must not wrap
it in a manually assembled B9 envelope.
"""

from __future__ import annotations

from ...contracts import OnboardRecordingQuery
from .onboard_playback_dates import PLAYBACK_GET_DATE_LIST_COMMAND
from .onboard_playback_modern import (
    PLAYBACK_GET_LIST_V1_COMMAND,
    PLAYBACK_GET_LIST_V2_COMMAND,
    build_modern_playback_list_v1_request,
    build_modern_playback_list_v2_request,
)
from .onboard_playback_types import PLAYBACK_GET_RECORDING_TYPES_COMMAND
from .onboard_playback_v34 import (
    build_modern_playback_list_v3_request,
    build_modern_playback_list_v4_request,
)
from .stream_protocol import build_builtin_command


def _command_for_protocol(protocol_version: int) -> int:
    if protocol_version == 1:
        return PLAYBACK_GET_LIST_V1_COMMAND
    if protocol_version in (2, 3, 4):
        return PLAYBACK_GET_LIST_V2_COMMAND
    raise ValueError("only recovered playback-list protocols V1 through V4 are supported")


def _build_list_body(
    query: OnboardRecordingQuery,
    page_index: int,
    protocol_version: int,
    count_per_page: int | None = None,
) -> bytes:
    builders = {
        1: build_modern_playback_list_v1_request,
        2: build_modern_playback_list_v2_request,
        3: build_modern_playback_list_v3_request,
        4: build_modern_playback_list_v4_request,
    }
    try:
        builder = builders[protocol_version]
    except KeyError as exc:
        raise ValueError(
            "only recovered playback-list protocols V1 through V4 are supported"
        ) from exc
    if count_per_page is not None:
        if protocol_version != 2:
            raise ValueError("a native page-size override is only certified for playback V2")
        return build_modern_playback_list_v2_request(
            query,
            page_index=page_index,
            count_per_page=count_per_page,
        )
    return builder(query, page_index=page_index)


def build_onboard_playback_list_message(
    query: OnboardRecordingQuery,
    request_id: int,
    *,
    page_index: int = 0,
    protocol_version: int = 2,
    count_per_page: int | None = None,
) -> bytes:
    """Build one recovered V1-V4 list message without a transport envelope."""

    return build_builtin_command(
        _command_for_protocol(protocol_version),
        _build_list_body(query, page_index, protocol_version, count_per_page),
        timestamp_us=request_id,
    )


def build_onboard_playback_date_message(
    query: OnboardRecordingQuery,
    request_id: int,
    *,
    page_index: int = 0,
    protocol_version: int = 2,
) -> bytes:
    """Build one recovered command-18 date-list message without a transport."""

    if protocol_version not in (2, 3, 4):
        raise ValueError("only playback-date protocols V2 through V4 are supported")
    return build_builtin_command(
        PLAYBACK_GET_DATE_LIST_COMMAND,
        _build_list_body(query, page_index, protocol_version),
        timestamp_us=request_id,
    )


def build_onboard_playback_recording_types_message(
    query: OnboardRecordingQuery,
    request_id: int,
    *,
    page_index: int = 0,
    protocol_version: int = 3,
) -> bytes:
    """Build one recovered command-15 type-list message without a transport."""

    if protocol_version not in (3, 4):
        raise ValueError("only playback recording-type protocols V3 and V4 are supported")
    return build_builtin_command(
        PLAYBACK_GET_RECORDING_TYPES_COMMAND,
        _build_list_body(query, page_index, protocol_version),
        timestamp_us=request_id,
    )
