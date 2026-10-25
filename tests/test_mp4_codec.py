"""Read local muxer codec metadata only through its bounded box hierarchy."""

import pytest

from backend.app.media.mp4_codec import h264_mime
from backend.app.media.mp4_fanout import MAX_INIT_BYTES


def box(kind, data):
    return (len(data) + 8).to_bytes(4, "big") + kind + data


def init(*, config=b"\x01\x42\xc0\x28\xff\xe1\x00", tracks=1, sample=b"avc1"):
    entry = box(sample, bytes(78) + box(b"avcC", config))
    value = box(b"stsd", bytes(4) + (1).to_bytes(4, "big") + entry)
    for kind in (b"stbl", b"minf", b"mdia", b"trak"):
        value = box(kind, value)
    return box(b"ftyp", b"isom") + box(b"moov", value * tracks)


def test_codec_level_comes_from_avcc_not_resolution_or_search_matches():
    assert h264_mime(init()) == ('video/mp4; codecs="avc1.42C028"', 40)
    assert h264_mime(init(config=b"\x01\x42\xe0\x1f\xff\xe1\x00")) == (
        'video/mp4; codecs="avc1.42E01F"', 31)


def av_init(*, config=b"\x14\x08", duplicate=False):
    def descriptor(tag, data):
        return bytes((tag, len(data))) + data
    decoder = descriptor(4, b"\x40\x15" + bytes(11) + descriptor(5, config))
    esds = box(b"esds", bytes(4) + descriptor(3, b"\0\1\0" + decoder))
    track = box(b"stsd", bytes(4) + (1).to_bytes(4, "big") + box(b"mp4a", bytes(28) + esds))
    for kind in (b"stbl", b"minf", b"mdia", b"trak"):
        track = box(kind, track)
    video = init()
    ftyp_size = int.from_bytes(video[:4], "big")
    return video[:ftyp_size] + box(b"moov", video[ftyp_size+8:] + track * (2 if duplicate else 1))


def test_optional_aac_lc_is_declared_from_actual_es_descriptor():
    assert h264_mime(av_init()) == ('video/mp4; codecs="avc1.42C028,mp4a.40.2"', 40)


@pytest.mark.parametrize("config", [b"", b"\x0c\x08", b"\x14"])
def test_missing_or_non_lc_audio_config_is_rejected(config):
    with pytest.raises(ValueError):
        h264_mime(av_init(config=config))


def test_duplicate_audio_tracks_are_rejected():
    with pytest.raises(ValueError):
        h264_mime(av_init(duplicate=True))


@pytest.mark.parametrize("case", ["multiple-tracks", "audio", "truncated", "fake-config",
                                  "high-profile", "oversize", "unbounded", "box-count"])
def test_invalid_or_unsupported_initialization_is_rejected(case):
    if case == "multiple-tracks":
        data = init(tracks=2)
    elif case == "audio":
        data = init(sample=b"mp4a")
    elif case == "truncated":
        data = init()[:-1]
    elif case == "fake-config":
        data = box(b"ftyp", box(b"avcC", b"\x01\x42\xc0\x28")) + box(b"moov", b"")
    elif case == "high-profile":
        data = init(config=b"\x01\x64\x00\x28\xff\xe1\x00")
    elif case == "oversize":
        data = bytes(MAX_INIT_BYTES + 1)
    elif case == "unbounded":
        data = b"\0\0\0\0ftyp" + bytes(8)
    else:
        data = box(b"ftyp", b"isom") + box(b"moov", box(b"free", b"") * 129)
    with pytest.raises(ValueError):
        h264_mime(data)
