"""Offline bounded decoder check. No sockets, media files or audio playback.

python -m scripts.validate_native_decode CAPTURE --flow flow5 --kind video
One selected flow/kind, at most 8 MiB elementary payload retained in memory.
FFprobe and strict FFmpeg run sequentially under prlimit; output is metadata only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Protocol

from backend.app.drivers.yoosee.p2p.stream_protocol import V1EncodingHeader
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record

from .replay_native_media import replay

MAX_BYTES = 8 * 1024 * 1024


class DecodeSample(Protocol):
    """In-memory input only; live callers must finish route cleanup before decoding."""

    flow: str
    kind: str
    data: bytearray
    frames: int
    encoding: V1EncodingHeader | None


class Sample:
    def __init__(self, flow: str, kind: str) -> None:
        if kind not in ("audio", "video"):
            raise ValueError("unknown media kind")
        self.flow, self.kind = flow, kind
        self.data = bytearray()
        self.frames = 0
        self.encoding: V1EncodingHeader | None = None

    def consume(self, label: str, record: V1Record) -> None:
        if label != self.flow:
            return
        if record.encoding is not None:
            if self.encoding is not None and self.encoding != record.encoding:
                raise ValueError("sample changes codec configuration")
            self.encoding = record.encoding
            return
        if self.encoding is None:
            raise ValueError("sample lacks initial encoding header")
        pieces = record.audio if self.kind == "audio" else ((record.video,) if record.video else ())
        for piece in pieces:
            if len(self.data) + len(piece) > MAX_BYTES:
                raise ValueError("sample exceeds 8 MiB budget")
            self.data.extend(piece)
            self.frames += 1


def validate(sample: DecodeSample) -> dict:
    if not sample.data or sample.encoding is None:
        raise ValueError("sample is empty or has no encoding header")
    expected = 5 if sample.kind == "video" else 4
    if getattr(sample.encoding, sample.kind + "_codec") != expected:
        raise ValueError("decoder not mapped for this codec")
    demuxer = "hevc" if sample.kind == "video" else "aac"
    stream = "v:0" if sample.kind == "video" else "a:0"
    limits = ["prlimit", "--as=536870912", "--cpu=30", "--nofile=64", "--"]
    input_args = ["-protocol_whitelist", "pipe", "-threads", "1", "-f", demuxer,
                  "-probesize", "1048576", "-analyzeduration", "5000000", "-i", "pipe:0"]
    probe = subprocess.run(
        [*limits, "ffprobe", "-v", "error", *input_args,
            "-select_streams", stream, "-count_frames", "-show_entries",
            "stream=codec_name,width,height,sample_rate,channels,nb_read_frames", "-of", "json"],
        input=sample.data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=45,
        check=False,
    )
    if probe.returncode != 0 or len(probe.stdout) > 4096:
        raise ValueError("independent probe failed")
    streams = json.loads(probe.stdout).get("streams", [])
    if len(streams) != 1 or int(streams[0].get("nb_read_frames", 0)) <= 0:
        raise ValueError("independent decoder found no frames")
    decode = subprocess.run(
        [*limits, "ffmpeg", "-v", "error", "-nostdin", "-xerror", "-err_detect", "explode",
                  "-filter_threads", "1", *input_args,
            "-map", "0:" + stream, "-threads", "1", "-f", "null", "-"],
        input=sample.data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45,
        check=False,
    )
    metadata = {key: value for key, value in streams[0].items() if key in {
        "codec_name", "width", "height", "sample_rate", "channels", "nb_read_frames"}}
    if sample.kind == "video":
        header_matches = (metadata.get("codec_name") == "hevc"
                          and metadata.get("width") == sample.encoding.video_width
                          and metadata.get("height") == sample.encoding.video_height)
    else:
        header_matches = (metadata.get("codec_name") == "aac"
                          and int(metadata.get("sample_rate", 0)) == sample.encoding.audio_sample_rate
                          and metadata.get("channels") == sample.encoding.audio_channels)
    return dict(flow=sample.flow, kind=sample.kind, input_frames=sample.frames,
                input_bytes=len(sample.data), strict_decode_ok=decode.returncode == 0,
                header_matches=header_matches,
                frame_count_matches=int(metadata["nb_read_frames"]) == sample.frames, decoder=metadata)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--flow", required=True)
    parser.add_argument("--kind", required=True, choices=("audio", "video"))
    args = parser.parse_args()
    try:
        sample = Sample(args.flow, args.kind)
        report = replay(args.capture, sample.consume)
        result = validate(sample)
        flow = next(flow for flow in report["flows"] if flow["flow"] == args.flow)
        result["transport_error"] = flow["error"]
        result["record_error"] = flow["media"]["records"]["error"]
        result["incomplete_tail_bytes"] = flow["media"]["records"]["incomplete_tail_bytes"]
        print(json.dumps(result, indent=2))
        if not result["strict_decode_ok"] or not result["header_matches"] or not result["frame_count_matches"]:
            raise SystemExit(2)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"Offline validation failed: {type(exc).__name__}") from None


if __name__ == "__main__":
    main()
