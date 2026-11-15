import threading

import pytest

from backend.app.drivers.yoosee.p2p.talk_inbox import TalkInbox
from backend.app.drivers.yoosee.p2p.talk_stream_input import MAX_QUEUED_FRAMES, StreamingTalkInput
from tests.test_av_receive import media
from tests.test_av_shared_talk import FRAME, ack, active, sent
from tests.test_media_receive import PEER, packet
from tests.test_v1_receive import av


def test_incremental_bursts_and_final_tail_do_not_wait_for_whole_recording():
    source = StreamingTalkInput()
    for _ in range(3):
        source.put(FRAME)
    assert not source.take_burst() and not source.finished
    source.put(FRAME)
    assert source.take_burst() == (FRAME,) * 4 and not source.finished
    source.put(FRAME)
    source.finish()
    assert source.take_burst() == (FRAME,) and source.finished


def test_backlog_and_total_duration_have_separate_bounds():
    source = StreamingTalkInput()
    for _ in range(MAX_QUEUED_FRAMES):
        source.put(FRAME)
    with pytest.raises(ValueError, match="backlog"):
        source.put(FRAME, timeout=.001)
    source.cancel()
    assert source.finished and source.aborted
    source = StreamingTalkInput()
    for _ in range(125):
        for _ in range(4):
            source.put(FRAME)
        assert len(source.take_burst()) == 4
    with pytest.raises(ValueError, match="duration"):
        source.put(FRAME)


def test_cancel_wakes_blocked_producer_without_waiting_on_av_thread(monkeypatch):
    source = StreamingTalkInput()
    for _ in range(MAX_QUEUED_FRAMES):
        source.put(FRAME)
    waiting = threading.Event()
    wait = source._condition.wait
    errors = []

    def observed_wait(timeout):
        waiting.set()
        return wait(timeout)

    monkeypatch.setattr(source._condition, "wait", observed_wait)
    def producer():
        try:
            source.put(FRAME)
        except ValueError as error:
            errors.append(str(error))
    worker = threading.Thread(target=producer)
    worker.start()
    try:
        assert waiting.wait(1)
    finally:
        source.cancel()
        worker.join(1)
    assert not worker.is_alive() and len(errors) == 1


def test_input_is_single_lease_and_single_consumer():
    source = StreamingTalkInput()
    inbox = TalkInbox(1, 2)
    inbox.submit(7, source)
    with pytest.raises(ValueError, match="already"):
        inbox.submit(8, source)
    source.claim()
    with pytest.raises(ValueError, match="already"):
        source.claim()
    inbox.close()
    assert source.aborted


def test_slow_but_nonidle_input_ends_before_whole_av_talk_deadline():
    now = [0.0]
    source = StreamingTalkInput(clock=lambda: now[0])
    now[0] = 10
    source.put(FRAME)
    now[0] = 11.001
    source.expire_producer()
    assert source.aborted and source.finished


def test_stream_transmits_before_eof_and_releases_only_microphone():
    now = [0.0]
    s = active(now)
    source = StreamingTalkInput(clock=lambda: now[0])
    talk = s.begin_talk(source)
    sent(s)  # header
    sent(s)  # microphone ON
    assert not s.due() and not talk.done
    for _ in range(4):
        source.put(FRAME)
    sent(s)
    assert talk.acknowledged_frames == 4 and not source.finished
    assert s.receive(packet(2, 0, media(av())), PEER).records[0].video == b"video"
    source.put(FRAME)
    source.finish()
    now[0] = .081
    sent(s)
    now[0] = .102
    sent(s)
    assert talk.done and talk.microphone_stopped and talk.acknowledged_frames == 5 and s.ready


def test_lost_microphone_producer_stops_utterance_without_poisoning_video():
    now = [0.0]
    s = active(now)
    source = StreamingTalkInput(clock=lambda: now[0])
    talk = s.begin_talk(source)
    sent(s)
    sent(s)
    now[0] = 2.001
    assert s.receive(packet(2, 0, media(av())), PEER).records[0].video == b"video"
    sent(s)  # OFF, not AV CLOSE.
    assert source.aborted and talk.cancelled and talk.done and talk.microphone_stopped and s.ready


def test_abandon_claimed_stream_keeps_slot_until_off_cleanup():
    source = StreamingTalkInput()
    inbox = TalkInbox(1, 1)
    ticket = inbox.submit(7, source)
    assert inbox.claim()[2] is source
    inbox.abandon(ticket)
    assert source.aborted and inbox.should_cancel(ticket)
    with pytest.raises(ValueError, match="slot"):
        inbox.submit(7, StreamingTalkInput())


def test_ten_second_paced_input_with_delayed_receipts_keeps_video_and_drains_off():
    from backend.app.drivers.yoosee.p2p.media_protocol import parse_kcp_segments

    now = [0.0]
    s = active(now)
    source = StreamingTalkInput(clock=lambda: now[0])
    talk = s.begin_talk(source)
    sent(s)
    sent(s)
    received, video_sequence, pending = 0, 2, {}
    for tick in range(1400):
        now[0] = tick * .01
        if tick % 2 == 0 and received < 500:
            source.put(FRAME)
            received += 1
            if received == 500:
                source.finish()
        if tick % 20 == 0:
            s.receive(packet(video_sequence, 0, media(av())), PEER)
            video_sequence += 1
        for wire in s.due():
            segment, = parse_kcp_segments(wire)
            pending.setdefault((segment.conv, segment.sequence), (now[0]+.35, wire))
        for key, (at, wire) in tuple(pending.items()):
            if at <= now[0]:
                s.receive(ack(wire), PEER)
                del pending[key]
        if talk.done:
            break
    assert received == talk.acknowledged_frames == 500
    assert talk.done and talk.microphone_stopped and not talk.cancelled and s.ready
    assert 10 <= now[0] < 13
