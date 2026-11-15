import threading
import time
from types import SimpleNamespace

import pytest

from backend.app.drivers.contracts import ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import shared_talk_stream as adapter
from backend.app.drivers.yoosee.p2p.talk_inbox import TalkOutcome
from tests.test_av_shared_talk import FRAME


class Encoder:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def feed(self, pcm, **kwargs): return (FRAME,) * (len(pcm) // 320)


@pytest.fixture
def owner(monkeypatch):
    state = SimpleNamespace(source=None, result=None, count=0, worker=None, claims=0)
    def receive():
        while True:
            state.count += len(state.source.take_burst())
            if state.source.finished:
                state.result = TalkOutcome("transport_received", state.count, True)
                return
            time.sleep(.001)
    def submit(source):
        state.claims += 1
        state.source = source
        source.bind()
        state.worker = threading.Thread(target=receive)
        state.worker.start()
        return "ticket"
    def abandon(ticket):
        if state.worker.is_alive():
            state.source.cancel()
        state.worker.join(1)
    session = SimpleNamespace(submit_talk=submit, take_talk=lambda _: state.result, abandon_talk=abandon)
    monkeypatch.setattr(adapter, "session_for", lambda _: session)
    monkeypatch.setattr(adapter, "AmrNbEncoder", Encoder)
    yield state
    if state.worker is not None:
        state.source.cancel()
        state.worker.join(1)
        assert not state.worker.is_alive()


def test_pcm_is_consumed_and_encoded_incrementally_without_second_session(owner):
    observed_before_eof = []
    def chunks():
        for _ in range(40):
            yield bytes(640)
        # More than the bounded25-frame input queue forces actual consumption.
        observed_before_eof.append(owner.count)
    result = adapter.send_stream(object(), chunks())
    assert observed_before_eof[0] >= 4
    assert result.completed and result.requested_frames == 40 and result.duration_ms == 800
    assert result.shared_session and not result.route_released and owner.claims == 1


@pytest.mark.parametrize("chunks", [[], [b"bad"], [bytes(640), b"bad"], [bytes(320640)]])
def test_bad_or_empty_pcm_abandons_input_and_cannot_report_success(owner, chunks):
    with pytest.raises(ControlOperationError):
        adapter.send_stream(object(), iter(chunks))
    assert owner.claims == 1 and not owner.worker.is_alive()


def test_no_native_owner_does_not_consume_microphone_or_create_encoder(monkeypatch):
    monkeypatch.setattr(adapter, "session_for", lambda _: None)
    monkeypatch.setattr(adapter, "AmrNbEncoder", lambda **_: pytest.fail("no encoder"))
    def chunks():
        pytest.fail("no microphone consumption")
        yield b""
    with pytest.raises(ControlNotReady):
        adapter.send_stream(object(), chunks())


def test_codec_failure_happens_before_microphone_submission(owner, monkeypatch):
    def fail(**kwargs):
        raise OSError("codec unavailable")
    monkeypatch.setattr(adapter, "AmrNbEncoder", fail)
    with pytest.raises(ControlOperationError, match="encoder unavailable"):
        adapter.send_stream(object(), iter((bytes(640),)))
    assert owner.claims == 0
