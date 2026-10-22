"""Offline synthetic HEVC -> bounded queue -> ongoing decoder. No camera access.

Run with PYTHONPATH=. inside an external memory/CPU/time-limited scope. The encoder
is sequential with the decoder; no image/audio output or temporary media files.
"""

import json
import subprocess
import time

from backend.app.drivers.yoosee.p2p.stream_protocol import V1EncodingHeader
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from backend.app.drivers.yoosee.p2p.video_consumer import VideoConsumer
from backend.app.drivers.yoosee.p2p.video_delivery import VideoDelivery
from backend.app.media.hevc_decoder import HevcDecoder


def main() -> None:
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

    def factory() -> HevcDecoder:
        value = HevcDecoder()
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
            if sent == 150 and decoders and decoders[0].frames >= 100:
                break
            time.sleep(0.002)
        if len(decoders) != 1 or decoders[0].frames < 100:
            raise RuntimeError("ongoing decode progress not established")
        result = {"submitted_frames": sent, "decoded_before_eof": decoders[0].frames,
                  "decoder_instances": len(decoders), "access_unit_bytes": len(encoded.stdout)}
    finally:
        consumer.close()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
