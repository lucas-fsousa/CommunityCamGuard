"""Offline synthetic HEVC -> bounded queue -> ongoing decoder. No camera access.

Run with PYTHONPATH=. inside an external memory/CPU/time-limited scope. The encoder
is sequential with the decoder; no image/audio output or temporary media files.
"""

import argparse
import json
import subprocess
import time

from backend.app.drivers.yoosee.p2p.stream_protocol import V1EncodingHeader
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from backend.app.drivers.yoosee.p2p.video_consumer import VideoConsumer
from backend.app.drivers.yoosee.p2p.video_delivery import VideoDelivery
from backend.app.media.fragmented_mp4 import MAX_BYTES, FragmentedMp4
from backend.app.media.hevc_decoder import HevcDecoder


def main(*, mp4: bool = False) -> None:
    encoded = subprocess.run([
        "prlimit", "--as=536870912", "--cpu=5", "--nofile=64", "--",
        "ffmpeg", "-v", "error", "-nostdin", "-threads", "1",
        "-filter_threads", "1", "-f", "lavfi", "-i", "color=black:s=64x64:r=25",
        "-frames:v", "1", "-c:v", "libx265", "-preset", "ultrafast",
        "-x265-params", "pools=none:frame-threads=1:log-level=error",
        "-f", "hevc", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=10, check=True)
    if not 0 < len(encoded.stdout) < 65536:
        raise RuntimeError("unexpected synthetic access unit")
    queue = VideoDelivery()
    decoders = []
    outputs: list[FragmentedMp4] = []
    captured = bytearray()

    def factory() -> HevcDecoder:
        output = FragmentedMp4() if mp4 else None
        value = HevcDecoder(output=output)
        if output is not None:
            outputs.append(output)
        decoders.append(value)
        return value

    consumer = VideoConsumer(queue, factory=factory)
    queue.consume(V1Record(encoding=V1EncodingHeader(264, 0, 0, 0, 0, 0, 0,
                                                   5, 25, 64, 64)))
    deadline = time.monotonic() + 10
    next_frame = time.monotonic()
    sent = 0
    try:
        while time.monotonic() < deadline:
            if time.monotonic() >= next_frame and sent < 150:
                sent += 1
                queue.consume(V1Record(video=encoded.stdout, video_timestamp=sent * 40000))
                next_frame = time.monotonic() + 0.02
            consumer.step()
            if outputs:
                while (chunk := outputs[-1].take()) is not None:
                    if len(captured) + len(chunk.data) > MAX_BYTES:
                        raise RuntimeError("synthetic output sample exceeded budget")
                    captured.extend(chunk.data)
            if sent == 150 and decoders and decoders[0].frames >= 100:
                break
            time.sleep(0.002)
        if len(decoders) != 1 or decoders[0].frames < 100:
            raise RuntimeError("ongoing decode progress not established")
        result = {"submitted_frames": sent, "decoded_before_eof": decoders[0].frames,
                  "decoder_instances": len(decoders), "access_unit_bytes": len(encoded.stdout)}
    finally:
        consumer.close()
    if mp4:
        # Inspect only after encoder/transcoder cleanup, keeping one child at a time.
        probe = subprocess.run([
            "prlimit", "--as=536870912", "--cpu=5", "--nofile=64", "--",
            "ffprobe", "-v", "error", "-protocol_whitelist", "pipe", "-threads", "1",
            "-i", "pipe:0", "-select_streams", "v:0", "-count_frames",
            "-show_entries", "stream=codec_name,codec_tag_string,pix_fmt,width,height,nb_read_frames",
            "-of", "json"], input=captured, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=10, check=True)
        if len(probe.stdout) > 4096:
            raise RuntimeError("probe output budget exceeded")
        streams = json.loads(probe.stdout)["streams"]
        if (len(streams) != 1 or streams[0]["codec_name"] != "h264"
                or streams[0]["codec_tag_string"] != "avc1" or streams[0]["pix_fmt"] != "yuv420p"
                or streams[0]["width"] != 64 or streams[0]["height"] != 64
                or int(streams[0]["nb_read_frames"]) < 50):
            raise RuntimeError("fragmented H264 output validation failed")
        result.update(mp4_bytes=len(captured), mp4_decoded_frames=int(streams[0]["nb_read_frames"]))
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mp4", action="store_true", help="validate H.264 fragmented MP4 output")
    main(mp4=parser.parse_args().mp4)
