"""Read baseline H264 and optional AAC-LC from our bounded local MP4 init.

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
    tracks = [payload for name, payload in boxes(one(top, b"moov")) if name == b"trak"]
    if not 1 <= len(tracks) <= 2:
        raise ValueError("unsupported MP4 track count")
    entries: dict[bytes, memoryview] = {}
    for container in tracks:
        for kind in (b"mdia", b"minf", b"stbl", b"stsd"):
            container = one(boxes(container), kind)
        if len(container) < 8 or bytes(container[:8]) != b"\0\0\0\0\0\0\0\1":
            raise ValueError("unsupported MP4 sample descriptions")
        samples = boxes(container[8:])
        if len(samples) != 1 or samples[0][0] not in (b"avc1", b"mp4a"):
            raise ValueError("H264 with optional AAC required")
        kind, payload = samples[0]
        if kind in entries:
            raise ValueError("duplicate MP4 codec track")
        entries[kind] = payload
    visual = entries.get(b"avc1")
    if visual is None or len(visual) < 78:
        raise ValueError("incomplete MP4 visual sample entry")
    config = one(boxes(visual[78:]), b"avcC")
    if len(config) < 7 or config[0] != 1 or config[1] != 66 or not config[3]:
        raise ValueError("baseline H264 configuration required")
    codec = bytes(config[1:4]).hex().upper()
    audio = entries.get(b"mp4a")
    if audio is not None:
        if len(audio) < 28 or bytes(audio[8:10]) != b"\0\0":
            raise ValueError("unsupported MP4 audio sample entry")
        esds = one(boxes(audio[28:]), b"esds")
        _validate_aac(esds)
    suffix = ",mp4a.40.2" if audio is not None else ""
    return f'video/mp4; codecs="avc1.{codec}{suffix}"', config[3]


def _validate_aac(esds: memoryview) -> None:
    """Validate the exact ES -> decoder -> AudioSpecificConfig path, not a byte search."""
    def descriptor(data: memoryview, tag: int) -> memoryview:
        if len(data) < 2 or data[0] != tag:
            raise ValueError("missing AAC descriptor")
        size = 0
        for index in range(1, min(5, len(data))):
            size = (size << 7) | (data[index] & 127)
            if not data[index] & 128:
                if index + 1 + size > len(data):
                    break
                return data[index + 1:index + 1 + size]
        raise ValueError("invalid AAC descriptor length")

    if len(esds) < 4 or bytes(esds[:4]) != bytes(4):
        raise ValueError("unsupported ES descriptor version")
    es = descriptor(esds[4:], 3)
    if len(es) < 3 or es[2] != 0:
        raise ValueError("unsupported ES descriptor flags")
    decoder = descriptor(es[3:], 4)
    if len(decoder) < 13 or decoder[0] != 0x40 or decoder[1] >> 2 != 5:
        raise ValueError("AAC audio decoder required")
    config = descriptor(decoder[13:], 5)
    if len(config) < 2 or config[0] >> 3 != 2:
        raise ValueError("AAC-LC configuration required")
