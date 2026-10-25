"""Native AAC has explicit format and independent fixed queue budgets."""

from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.p2p.av_delivery import MAX_AUDIO_FRAMES, NativeAvDelivery
from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from tests.test_captured_media import header

ADTS = b"\xff\xf1\x60\x40\0\xff\xfc"


def delivery():
    value = NativeAvDelivery()
    value.consume(V1Record(encoding=unpack_v1_encoding_header(header())))
    return value


def test_audio_batch_preserves_sample_clock_and_close_discards_private_frames():
    value = delivery()
    value.consume(V1Record(audio=(ADTS, ADTS), audio_timestamp=1_000_000))
    assert value.take_audio().timestamp == 1_000_000
    assert value.take_audio().timestamp == 1_064_000
    assert value.take_audio() is None
    value.consume(V1Record(audio=(ADTS,)))
    value.close()
    value.consume(V1Record(audio=(ADTS,)))
    assert value.take_audio() is None


def test_audio_backlog_is_bounded_instead_of_silently_desynchronizing():
    value = delivery()
    value.consume(V1Record(audio=(ADTS,) * MAX_AUDIO_FRAMES))
    with pytest.raises(ValueError, match="overflow"):
        value.consume(V1Record(audio=(ADTS,)))
    value.close()


@pytest.mark.parametrize("changes", [{"audio_codec":1}, {"audio_sample_rate":8000}, {"audio_channels":2}])
def test_unreviewed_audio_header_is_not_inferred_supported(changes):
    value = NativeAvDelivery()
    original = unpack_v1_encoding_header(header())
    with pytest.raises(ValueError, match="not validated"):
        value.consume(V1Record(encoding=replace(original, **changes)))


def test_non_adts_audio_is_rejected():
    with pytest.raises(ValueError, match="ADTS"):
        delivery().consume(V1Record(audio=(b"not-aac",)))


@pytest.mark.parametrize("index,value", [(2,0x20), (3,0x80), (4,1)])
def test_adts_must_match_reviewed_codec_and_declared_frame_length(index, value):
    frame = bytearray(ADTS)
    frame[index] = value
    with pytest.raises(ValueError, match="ADTS"):
        delivery().consume(V1Record(audio=(bytes(frame),)))
