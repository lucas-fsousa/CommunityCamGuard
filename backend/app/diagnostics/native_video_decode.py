"""Bounded, sequential, pipe-only HEVC validation after camera-route teardown."""

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from ..drivers.yoosee.p2p.av_sample import MAX_BYTES, MAX_FRAMES, AvVideoSample
from ..drivers.yoosee.p2p.contracts import P2PProbeError


@dataclass(frozen=True, slots=True)
class DecodedVideo:
    frames: int
    width: int
    height: int
    input_bytes: int
    timestamp_span_ticks: int
    pre_idr_discarded: int


def require_decoder_tools() -> None:
    if any(shutil.which(name) is None for name in ("prlimit", "ffprobe", "ffmpeg")):
        raise P2PProbeError("native video decoder tools unavailable")


def decode_sample(sample: AvVideoSample, *,
                  cancelled: Callable[[], bool] = lambda: False) -> DecodedVideo:
    """No file/network protocols, image output or audio playback. Caller clears sample.

    Two sequential children: each <=512 MiB address space, 10 CPU seconds and
    15 seconds wall time. Cancellation is checked between children, not mid-decode.
    """
    try:
        require_decoder_tools()
        encoding = sample.encoding
        if (cancelled() or sample.closed or encoding is None or encoding.video_codec != 5
                or not 0 < len(sample.data) <= MAX_BYTES or not 0 < sample.frames <= MAX_FRAMES
                or not 0 < encoding.video_width <= 1920 or not 0 < encoding.video_height <= 1080):
            raise ValueError("invalid sample")
        limits = ["prlimit", "--as=536870912", "--cpu=10", "--nofile=64", "--"]
        source = ["-protocol_whitelist", "pipe", "-threads", "1", "-f", "hevc",
                  "-probesize", "1048576", "-analyzeduration", "5000000", "-i", "pipe:0"]
        probe = subprocess.run(
            [*limits, "ffprobe", "-v", "error", *source, "-select_streams", "v:0",
             "-count_frames", "-show_entries", "stream=codec_name,width,height,nb_read_frames",
             "-of", "json"], input=sample.data, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=15, check=False,
        )
        if probe.returncode or len(probe.stdout) > 4096 or cancelled():
            raise ValueError("probe failed")
        streams = json.loads(probe.stdout)["streams"]
        if len(streams) != 1:
            raise ValueError("invalid stream count")
        stream = streams[0]
        if (stream["codec_name"] != "hevc" or stream["width"] != encoding.video_width
                or stream["height"] != encoding.video_height
                or int(stream["nb_read_frames"]) != sample.frames):
            raise ValueError("decoder disagrees with sample")
        decoded = subprocess.run(
            [*limits, "ffmpeg", "-v", "error", "-nostdin", "-xerror", "-err_detect", "explode",
             "-filter_threads", "1", *source, "-map", "0:v:0", "-threads", "1", "-f", "null", "-"],
            input=sample.data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=False,
        )
        if decoded.returncode or cancelled():
            raise ValueError("strict decode failed")
        return DecodedVideo(sample.frames, encoding.video_width, encoding.video_height,
                            len(sample.data), sample.timestamp_span_ticks, sample.discarded)
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.TimeoutExpired):
        raise P2PProbeError("native video decode validation failed") from None
