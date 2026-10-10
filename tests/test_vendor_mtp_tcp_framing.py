import pytest

from backend.app.drivers.yoosee.p2p.media_protocol import build_mtp_frame
from backend.app.drivers.yoosee.p2p.mtp_tcp_framing import (
    MAX_READ_BYTES,
    MAX_RECORDS_PER_FEED,
    MtpTcpFrameError,
    MtpTcpFramer,
)


@pytest.mark.parametrize("prefix", [0x10, 0x50, 0x90, 0xD0])
def test_all_split_points_and_single_byte_reads(prefix):
    wire = build_mtp_frame(prefix, bytes(80))
    for split in range(len(wire) + 1):
        framer = MtpTcpFramer()
        assert framer.feed(wire[:split]) + framer.feed(wire[split:]) == [wire]
        framer.finish()
    framer = MtpTcpFramer()
    records = []
    for byte in wire:
        records.extend(framer.feed(bytes((byte,))))
    assert records == [wire]
    assert framer.buffered_bytes == 0


def test_mixed_coalesced_records_keep_only_incomplete_tail():
    meter = build_mtp_frame(0xD0, bytes(76))
    kcp = build_mtp_frame(0x50, bytes(32))
    framer = MtpTcpFramer()
    assert framer.feed(meter + kcp + meter[:17]) == [meter, kcp]
    assert framer.buffered_bytes == 17
    assert framer.feed(meter[17:]) == [meter]


def test_maximum_record_and_batch_limits():
    wire = build_mtp_frame(0x50, bytes(1494))
    framer = MtpTcpFramer()
    assert framer.feed(wire[:-1]) == []
    assert framer.buffered_bytes == 1499
    assert framer.feed(wire[-1:]) == [wire]
    short = build_mtp_frame(0x10, bytes(24))
    assert len(framer.feed(short * MAX_RECORDS_PER_FEED)) == MAX_RECORDS_PER_FEED
    with pytest.raises(MtpTcpFrameError, match="batch"):
        framer.feed(short * (MAX_RECORDS_PER_FEED + 1))
    assert framer.buffered_bytes == 0


@pytest.mark.parametrize("size", [0, 6, 29, 1501, 2047])
def test_invalid_length_rejected_from_prefix_only(size):
    framer = MtpTcpFramer()
    with pytest.raises(MtpTcpFrameError, match="length"):
        framer.feed(bytes((0xC0, 0x50, size & 7, size >> 3)))
    with pytest.raises(MtpTcpFrameError, match="closed"):
        framer.feed(b"")


@pytest.mark.parametrize("prefix", [0, 0x60, 0x80, 0xE0, 0x30, 0x70])
def test_unknown_or_outgoing_prefix_never_resynchronizes(prefix):
    framer = MtpTcpFramer()
    with pytest.raises(MtpTcpFrameError, match="prefix"):
        framer.feed(build_mtp_frame(prefix, bytes(24)))
    with pytest.raises(MtpTcpFrameError, match="closed"):
        framer.feed(build_mtp_frame(0x10, bytes(24)))


def test_bad_checksum_discards_entire_batch_and_partial_storage():
    good = build_mtp_frame(0x10, bytes(24))
    bad = bytearray(good)
    bad[4] ^= 1
    framer = MtpTcpFramer()
    with pytest.raises(MtpTcpFrameError, match="checksum"):
        framer.feed(good + bytes(bad))
    assert framer.buffered_bytes == 0


def test_eof_and_abort_do_not_allow_reuse():
    wire = build_mtp_frame(0x10, bytes(24))
    for split in range(1, len(wire)):
        framer = MtpTcpFramer()
        framer.feed(wire[:split])
        with pytest.raises(MtpTcpFrameError, match="truncated"):
            framer.finish()
        assert framer.buffered_bytes == 0
    for terminate in ("finish", "abort"):
        framer = MtpTcpFramer()
        getattr(framer, terminate)()
        framer.abort()
        with pytest.raises(MtpTcpFrameError, match="closed"):
            framer.feed(wire)


@pytest.mark.parametrize("data", [bytes(MAX_READ_BYTES + 1), bytearray(1), "x"])
def test_read_policy_is_terminal(data):
    framer = MtpTcpFramer()
    with pytest.raises(MtpTcpFrameError, match="admission"):
        framer.feed(data)
    assert framer.buffered_bytes == 0
