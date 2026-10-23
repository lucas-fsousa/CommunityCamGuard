"""Read the codec declaration from our bounded, video-only local MP4 init.

This is not a general-purpose MP4 validator. Walk the exact muxer container path
instead of searching private payload bytes for an avcC-looking substring.
"""

from .mp4_fanout import MAX_INIT_BYTES


def h264_mime(initialization: bytes) -> tuple[str, int]:
    if type(initialization) is not bytes or not 16 <= len(initialization) <= MAX_INIT_BYTES:
        raise ValueError("invalid MP4 initialization")
    remaining = 128

    def boxes(data: memoryview) -> list[tuple[bytes, memoryview]]:
        nonlocal remaining
        result = []
        while data:
            remaining -= 1
            if remaining < 0 or len(data) < 8:
                raise ValueError("invalid MP4 container")
            size = int.from_bytes(data[:4], "big")
            if not 8 <= size <= len(data):
                raise ValueError("invalid MP4 box size")
            result.append((bytes(data[4:8]), data[8:size]))
            data = data[size:]
        return result

    def one(children: list[tuple[bytes, memoryview]], kind: bytes) -> memoryview:
        matches = [payload for name, payload in children if name == kind]
        if len(matches) != 1:
            raise ValueError("unsupported MP4 track layout")
        return matches[0]

    top = boxes(memoryview(initialization))
    if [name for name, _ in top] != [b"ftyp", b"moov"]:
        raise ValueError("invalid MP4 initialization order")
    container = one(top, b"moov")
    for kind in (b"trak", b"mdia", b"minf", b"stbl", b"stsd"):
        container = one(boxes(container), kind)
    # stsd FullBox header followed by exactly one visual sample entry.
    if len(container) < 8 or bytes(container[:8]) != b"\0\0\0\0\0\0\0\1":
        raise ValueError("unsupported MP4 sample descriptions")
    samples = boxes(container[8:])
    if len(samples) != 1 or samples[0][0] != b"avc1":
        raise ValueError("H264 video-only output required")
    visual = samples[0][1]
    if len(visual) < 78:
        raise ValueError("incomplete MP4 visual sample entry")
    config = one(boxes(visual[78:]), b"avcC")
    if len(config) < 7 or config[0] != 1 or config[1] != 66 or not config[3]:
        raise ValueError("baseline H264 configuration required")
    codec = bytes(config[1:4]).hex().upper()
    return f'video/mp4; codecs="avc1.{codec}"', config[3]
