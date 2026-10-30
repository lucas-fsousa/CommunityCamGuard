import pytest

from backend.app.media.fragmented_mp4 import (
    MAX_BYTES,
    MAX_CHUNKS,
    FragmentedMp4,
    Mp4OrderError,
    Mp4SizeError,
)


def box(kind, payload=b"test"):
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


@pytest.mark.parametrize("actual,label", [(b"free","free"),(b"pw!x","unknown")])
def test_box_order_diagnostics_never_include_arbitrary_media_bytes(actual,label):
    output=FragmentedMp4()
    with pytest.raises(Mp4OrderError) as caught:
        output.feed(box(actual))
    assert caught.value.expected=="ftyp" and caught.value.actual==label
    assert "pw!x" not in str(caught.value) and output.closed


def test_split_boxes_emit_only_complete_initialization_and_fragment_pairs():
    output = FragmentedMp4()
    init = box(b"ftyp") + box(b"moov")
    fragment = box(b"moof") + box(b"mdat", b"private-video")
    for byte in init + fragment:
        output.feed(bytes([byte]))
    first, second = output.take(), output.take()
    assert first.initialization and first.data == init
    assert not second.initialization and second.data == fragment
    assert "private-video" not in repr(second)
    assert output.take() is None and output.buffered_bytes == 0
    assert output.started


@pytest.mark.parametrize("data", [box(b"moov"), box(b"moof"), b"\0\0\0\0ftyp",
                                   b"\0\0\0\1ftyp", (MAX_BYTES + 1).to_bytes(4, "big") + b"ftyp"],
                         ids=["missing-ftyp", "missing-init", "unbounded", "extended", "oversized"])
def test_invalid_muxer_framing_retires_output(data):
    output = FragmentedMp4()
    with pytest.raises(ValueError):
        output.feed(data)
    assert output.closed and output.buffered_bytes == 0


def test_fragment_must_be_paired_and_cannot_replace_initialization():
    output = FragmentedMp4()
    output.feed(box(b"ftyp") + box(b"moov") + box(b"moof"))
    assert output.take().initialization
    assert output.take() is None
    with pytest.raises(ValueError):
        output.feed(box(b"moov"))
    assert output.closed


def test_slow_consumer_fails_instead_of_dropping_dependent_fragments():
    output = FragmentedMp4()
    output.feed(box(b"ftyp") + box(b"moov"))
    for _ in range(MAX_CHUNKS - 1):
        output.feed(box(b"moof") + box(b"mdat"))
    with pytest.raises(ValueError, match="stalled"):
        output.feed(box(b"moof") + box(b"mdat"))
    assert output.closed and output.take() is None


def test_byte_budget_counts_partial_boxes_and_queued_chunks_together():
    output = FragmentedMp4()
    output.feed(box(b"ftyp", b"x" * (MAX_BYTES - 8)))
    assert output.buffered_bytes == MAX_BYTES
    with pytest.raises(ValueError):
        output.feed(b"x")
    assert output.buffered_bytes == 0 and output.closed


def test_close_discards_private_bytes_and_is_idempotent():
    output = FragmentedMp4()
    output.feed(box(b"ftyp"))
    output.close()
    output.close()
    assert output.buffered_bytes == 0
    with pytest.raises(ValueError):
        output.feed(b"")


def test_output_can_only_be_claimed_once_even_before_first_bytes():
    output = FragmentedMp4()
    output.claim()
    with pytest.raises(ValueError, match="fresh"):
        output.claim()


@pytest.mark.parametrize('size,kind,label', [(0,b'mdat','mdat'),(MAX_BYTES+1,b'mdat','mdat'),(1,b'priv','unknown')])
def test_size_failure_retains_only_numeric_size_and_allowlisted_type(size,kind,label):
    output = FragmentedMp4()
    with pytest.raises(Mp4SizeError) as caught:
        output.feed(size.to_bytes(4,'big')+kind)
    assert caught.value.declared_size == size and caught.value.box_type == label
    assert 'priv' not in str(caught.value) and output.closed
