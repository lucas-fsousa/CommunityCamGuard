"""Live-view quality policy.

Maps a **quality level** to the encoder parameters used by the browser-facing H.264
transcodes. Kept separate from the go2rtc plumbing (:mod:`.go2rtc`) so the policy is a pure,
unit-testable function with no process/IO dependency, and so a future per-vendor or
per-host policy has one obvious place to live.

Background (see ``docs/DECISIONS.md §34``): the cameras emit HEVC with no PTS, so the browser
feed **must** be re-encoded to H.264. Until now that transcode ran on go2rtc's built-in
defaults with **no explicit bitrate** — i.e. libx264's constant-quality default. This module
makes the bitrate (the lever that actually buys picture quality at a fixed resolution) an
explicit, level-driven choice.

The bitrate is chosen per *source resolution*, not per variant name: more bits on a 640x360
substream add little (the source is ~37 kbps), whereas the 1080p main feed is where a healthy
target bitrate visibly helps. Callers pass ``hd=True`` when the transcode source is the main
feed.
"""
from __future__ import annotations

from typing import Literal

QualityLevel = Literal["low", "medium", "high", "max"]

#: Ordered from cheapest/softest to sharpest/most expensive. ``max`` is the default: the doc's
#: intent is "the most quality the stream and camera can give". The *host* budget is enforced
#: separately by ``grid_hd_max_cameras`` (which view uses the main feed at all), not here.
LEVELS: tuple[QualityLevel, ...] = ("low", "medium", "high", "max")

DEFAULT_LEVEL: QualityLevel = "max"

#: Target video bitrate (kbps) for a **substream** source (~640x360 on these units).
_SUB_KBPS: dict[str, int] = {"low": 150, "medium": 300, "high": 500, "max": 800}
#: Target video bitrate (kbps) for a **main-feed** source (1080p). §34 measured go2rtc's
#: default main transcode at ~3000 kbps; ``high``/``max`` sit around and above that.
_MAIN_KBPS: dict[str, int] = {"low": 1200, "medium": 2000, "high": 3000, "max": 4500}

#: Keyframe interval in seconds. A ~2 s GOP keeps WebRTC/MSE seeking snappy without spending
#: many bits on I-frames at these low frame rates.
GOP_SECONDS: float = 2.0


def normalize_level(level: str | None) -> QualityLevel:
    """Coerce arbitrary input to a known level, falling back to :data:`DEFAULT_LEVEL`."""
    if level in LEVELS:
        return level
    return DEFAULT_LEVEL


#: Hardware transcoders go2rtc understands (``#hardware=<x>``). Empty = software libx264, the
#: safe default. Hardware encoding is what makes a *sharp* grid affordable on a weak host — it
#: moves the H.264 encode off the CPU — but it is device-specific (VAAPI needs ``/dev/dri``,
#: NVENC needs the NVIDIA runtime, and the go2rtc build must include the codec), so it is opt-in
#: and must be validated on the actual box. See ``docs/DECISIONS.md §34`` for the CPU ceiling.
HWACCELS: tuple[str, ...] = ("", "vaapi", "cuda", "dxva2", "videotoolbox", "v4l2m2m", "rkmpp")


def normalize_hwaccel(hwaccel: str | None) -> str:
    """Coerce to a known go2rtc hardware value; unknown/empty -> "" (software encode)."""
    v = (hwaccel or "").strip().lower()
    return v if v in HWACCELS and v else ""


def video_h264_directive(hwaccel: str | None = "") -> str:
    """The go2rtc video-codec directive for a transcode: ``#video=h264`` plus optional hardware.

    With a hardware value it becomes e.g. ``#video=h264#hardware=vaapi``, telling go2rtc to encode
    with ``h264_vaapi`` instead of libx264. Unknown/empty falls back to plain software H.264.
    """
    hw = normalize_hwaccel(hwaccel)
    return f"#video=h264#hardware={hw}" if hw else "#video=h264"


def target_kbps(level: str | None, *, hd: bool) -> int:
    """Target video bitrate (kbps) for ``level`` on a main-feed (``hd``) or substream source."""
    table = _MAIN_KBPS if hd else _SUB_KBPS
    return table[normalize_level(level)]


def encode_raw_args(level: str | None, fps: int, *, hd: bool) -> str:
    """Build the go2rtc ``#raw=`` chunk for a transcode: fixed frame rate + quality bitrate/GOP.

    ``#raw=`` appends raw ffmpeg **output** args to go2rtc's ``#video=h264`` template. We set:

    * ``-r {fps}`` — the fixed rate the browser needs (these cameras send no PTS; see ``live_fps``).
    * ``-b:v/-maxrate/-bufsize`` — an explicit capped bitrate: the quality lever this module adds.
    * ``-g`` — a ~2 s keyframe interval derived from the frame rate.

    Returned as a single ``#raw=`` segment (go2rtc takes one raw block, space-separated args).
    """
    kbps = target_kbps(level, hd=hd)
    gop = max(1, round(fps * GOP_SECONDS))
    return f"#raw=-r {fps} -b:v {kbps}k -maxrate {kbps}k -bufsize {kbps * 2}k -g {gop}"
