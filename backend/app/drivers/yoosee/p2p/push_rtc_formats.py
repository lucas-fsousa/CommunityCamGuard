"""Offline format descriptors, traced through BasePlayer and AVInput consumers."""

import math
import struct
from dataclasses import dataclass, field

from .push_rtc_codecs import rtc_avcodec_id, rtc_codec_name


@dataclass(frozen=True)
class RTCVideoFormat:
    stream_index: int
    codec_id: int
    width: int
    height: int
    frame_rate: float
    raw: bytes = field(repr=False)

    @property
    def avcodec_id(self) -> int | None:
        return rtc_avcodec_id("video", self.codec_id)

    @property
    def codec_name(self) -> str | None:
        return rtc_codec_name("video", self.codec_id)


@dataclass(frozen=True)
class RTCAudioFormat:
    stream_index: int  # Before BasePlayer's video-map-size offset.
    codec_id: int
    codec_option: int
    channels: int
    bit_width: int
    sample_rate: int
    frame_size: int
    raw: bytes = field(repr=False)

    @property
    def avcodec_id(self) -> int | None:
        return rtc_avcodec_id("audio", self.codec_id)

    @property
    def codec_name(self) -> str | None:
        return rtc_codec_name("audio", self.codec_id)


def parse_rtc_format(entry: bytes) -> RTCVideoFormat | RTCAudioFormat:
    """Interpret one twenty-byte descriptor, without decoder/capability changes.

    Unknown kinds fail closed; unknown codec enums remain unknown. Zero values
    are preserved rather than replaced by defaults. Nonfinite rates are rejected.
    This is metadata extraction, not permission to allocate a decoder or trust
    claimed dimensions/rates. Preserve the raw entry for uninterpreted fields.
    """
    if not isinstance(entry, bytes) or len(entry) != 20:
        raise ValueError("RTC format descriptor must contain twenty immutable bytes")
    index = max(0, entry[2] - 1)
    if entry[1] == 1:
        width, height, rate = struct.unpack_from("<HHf", entry, 4)
        if not math.isfinite(rate):
            raise ValueError("RTC video frame rate is not finite")
        return RTCVideoFormat(index, entry[12], width, height, rate, entry)
    if entry[1] == 2:
        sample_rate, frame_size = struct.unpack_from("<IH", entry, 4)
        return RTCAudioFormat(index, entry[12], entry[13], entry[10], entry[11],
                              sample_rate, frame_size, entry)
    raise ValueError("RTC format descriptor kind is unsupported")
