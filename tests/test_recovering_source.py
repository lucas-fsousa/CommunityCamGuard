"""Recovery order and resource exclusion without threads, cameras or real waits."""

from itertools import pairwise

import pytest

from backend.app.media import recovering_source as module
from backend.app.media.mp4_fanout import StreamClosedError
from backend.app.media.source_registry import (
    SourceCleanupUnconfirmed,
    SourceRegistry,
    SourceUnavailable,
)

CAMERA = "cam_" + "a" * 24


class Source:
    def __init__(self, result, events, *, error=None):
        self.result, self.events, self.error = result, events, error
        self.retired = False
    def run(self, *, cancelled):
        self.events.append("run")
        if self.error: raise self.error
        self.retired = True
        self.events.append("retired")
        return self.result
    def subscribe(self):
        if self.retired: raise StreamClosedError("retired")
        return "reader"


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    def sleep(duration): now[0] += duration
    monkeypatch.setattr(module.time, "sleep", sleep)
    return now


def test_replacement_waits_for_close_and_backoff_keeps_same_registry_owner(clock, monkeypatch):
    events = []
    first, second = Source("socket", events), Source("stop", events)
    registry = SourceRegistry()
    def construct():
        assert first.retired and clock[0] >= 2
        events.append("construct")
        return second
    wrapper = module.RecoveringSource(first, factory=construct, valid=lambda: True,
        retryable=lambda result, _: result == "socket", clock=lambda: clock[0])
    def sleep(duration):
        assert wrapper.recovering and registry.state(CAMERA) == "running"
        assert registry.states()[CAMERA] == "recovering"
        assert registry.camera_ids() == (CAMERA,)
        with pytest.raises(StreamClosedError): wrapper.subscribe()
        with pytest.raises(SourceUnavailable):
            registry.run("cam_" + "b" * 24, domain="shared", factory=lambda: second, cancelled=lambda: False)
        clock[0] += duration
    monkeypatch.setattr(module.time, "sleep", sleep)
    assert registry.run(CAMERA, domain="shared", factory=lambda: wrapper, cancelled=lambda: False) == "stop"
    assert events == ["run", "retired", "construct", "run", "retired"]
    assert wrapper.restarts == 1 and not wrapper.recovering and registry.state(CAMERA) == "idle"


def test_uncertain_cleanup_never_constructs_replacement_and_keeps_registry_blocked(clock):
    first = Source(None, [], error=SourceCleanupUnconfirmed("unknown STOP or TCP close"))
    wrapper = module.RecoveringSource(first, factory=lambda: pytest.fail("replacement"),
        valid=lambda: True, retryable=lambda *_: True, clock=lambda: clock[0])
    registry = SourceRegistry()
    with pytest.raises(SourceCleanupUnconfirmed):
        registry.run(CAMERA, domain="shared", factory=lambda: wrapper, cancelled=lambda: False)
    assert registry.state(CAMERA) == "blocked" and wrapper.current is first and wrapper.restarts == 0


@pytest.mark.parametrize("action", ["cancel", "revoke"])
def test_backoff_observes_cancellation_and_revocation_without_replacement(clock, monkeypatch, action):
    state = {"cancel":False,"valid":True}
    first = Source("socket", [])
    wrapper = module.RecoveringSource(first, factory=lambda: pytest.fail("replacement"),
        valid=lambda: state["valid"], retryable=lambda *_: True, clock=lambda: clock[0])
    def sleep(duration):
        clock[0] += duration
        state["cancel" if action == "cancel" else "valid"] = action == "cancel"
    monkeypatch.setattr(module.time, "sleep", sleep)
    assert wrapper.run(cancelled=lambda: state["cancel"]) == "socket"
    assert first.retired and wrapper.restarts == 0 and clock[0] <= 1.2


def test_backoff_grows_is_capped_and_resets_after_healthy_run(clock):
    starts = []
    remaining = [9]
    class TimedSource(Source):
        def run(self, *, cancelled):
            starts.append(clock[0])
            if remaining[0] == 1:
                clock[0] += 60
            remaining[0] -= 1
            return super().run(cancelled=cancelled)
    def construct(): return TimedSource("socket", [])
    wrapper = module.RecoveringSource(construct(), factory=construct, valid=lambda: True,
        retryable=lambda *_: remaining[0] >= 0, clock=lambda: clock[0])
    wrapper.run(cancelled=lambda: False)
    gaps = [round(b-a,2) for a,b in pairwise(starts)]
    assert gaps == [2,4,8,16,32,60,60,60,62]
    assert wrapper.restarts == 9


def test_initial_revocation_and_second_run_are_rejected_without_source_io(clock):
    source = Source(None, [])
    wrapper = module.RecoveringSource(source, factory=lambda: pytest.fail("factory"), valid=lambda: False,
        retryable=lambda *_: True, clock=lambda: clock[0])
    with pytest.raises(SourceUnavailable): wrapper.run(cancelled=lambda: False)
    with pytest.raises(ValueError): wrapper.run(cancelled=lambda: False)
    assert not source.events


def test_cancellation_during_factory_does_not_start_replacement(clock):
    cancelled = [False]
    first, second = Source("socket", []), Source(None, [])
    def construct():
        cancelled[0] = True
        return second
    wrapper = module.RecoveringSource(first, factory=construct, valid=lambda: True,
        retryable=lambda *_: True, clock=lambda: clock[0])
    assert wrapper.run(cancelled=lambda: cancelled[0]) == "socket"
    assert first.retired and not second.events
