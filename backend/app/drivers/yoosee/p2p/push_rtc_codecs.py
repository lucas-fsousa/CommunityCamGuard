"""Pinned SDK 6.45 codec-ID translation, not camera capability discovery."""

from typing import Literal

_AUDIO_IDS = {1: 65543, 2: 65542, 3: 69643, 4: 86018, 5: 73728, 6: 69641, 7: 86076}
_VIDEO_IDS = {1: 27, 2: 12, 3: 88, 4: 7, 5: 173}


def rtc_avcodec_id(media_kind: Literal["audio", "video"], codec_id: int) -> int | None:
    """Translate an explicitly identified SDK codec enum to its AVCodecID.

    Unknown values return None; no default codec, byte truncation, or inference
    from model/platform. Caller must establish descriptor field provenance.
    A known enum alone does not establish playable media or device support.
    """
    if media_kind not in ("audio", "video"):
        raise ValueError("RTC codec media kind is unsupported")
    if type(codec_id) is not int or not 0 <= codec_id <= 255:
        raise ValueError("RTC codec ID must be an unsigned byte")
    mapping = _AUDIO_IDS if media_kind == "audio" else _VIDEO_IDS
    return mapping.get(codec_id)
