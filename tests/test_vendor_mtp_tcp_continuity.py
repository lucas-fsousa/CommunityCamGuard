"""Synthetic continuity/cleanup checks; no sockets, sleeps or camera commands."""

import pytest

from backend.app.drivers.yoosee.p2p.kcp_receive import ReceiveError
from tests.test_av_handshake import ack
from tests.test_av_receive import media
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av
from tests.test_vendor_mtp_tcp_av import canonical, incoming, negotiate, session


@pytest.mark.parametrize("chunk_size", [1, 13, 74, 4096])
def test_continuous_media_preserves_order_without_retained_backlog(chunk_size):
    now = [1.0]
    owner = session(now)
    negotiate(owner)
    for index in range(100):
        now[0] += 0.02
        payload = index.to_bytes(4, "little")
        wire = incoming(packet(index + 2, 0, media(av(video=payload, video_ts=index))))
        records = []
        # A retransmission must be acknowledged, not delivered a second time.
        stream = wire + wire
        for offset in range(0, len(stream), chunk_size):
            batch = owner.receive(stream[offset:offset + chunk_size], PEER)
            records.extend(batch.records)
        assert [record.video for record in records] == [payload]
        assert [record.video_timestamp for record in records] == [index]
        assert owner.ready and owner.buffered_bytes == 0
        assert not owner.due()
    owner.begin_finish()
    close, = owner.due()
    owner.receive(incoming(ack(canonical(close))), PEER)
    assert owner.close_acknowledged
    owner.finish()
    assert owner.closed and owner.buffered_bytes == 0


def test_missing_media_fragment_expires_and_cannot_resume_old_session():
    now = [1.0]
    owner = session(now)
    negotiate(owner)
    # Sequence 2 never arrives: acknowledge/buffer 3 only within the fixed budget.
    wire = incoming(packet(3, 0, media(av())))
    assert not owner.receive(wire, PEER).records
    assert owner.buffered_bytes > 0
    now[0] += 2.0
    with pytest.raises(ReceiveError):
        owner.poll()
    assert owner.closed and owner.buffered_bytes == 0
    with pytest.raises(ReceiveError):
        owner.receive(incoming(packet(2, 0, media(av()))), PEER)
