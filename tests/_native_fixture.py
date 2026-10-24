"""Bounded synthetic MP4 structure; not decodable camera/media data."""


def box(kind, payload):
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def initialization():
    config = box(b"avcC", b"\x01\x42\xc0\x1e\xff\xe1\x00")
    value = b"\0\0\0\0\0\0\0\1" + box(b"avc1", bytes(78) + config)
    for kind in (b"stsd", b"stbl", b"minf", b"mdia", b"trak", b"moov"):
        value = box(kind, value)
    return box(b"ftyp", b"isom") + value


INIT = initialization()
FRAGMENT = box(b"moof", b"sequence") + box(b"mdat", b"synthetic-video")
