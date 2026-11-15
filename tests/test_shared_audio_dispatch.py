"""Unselected future route: no real profile grant, camera, microphone or network."""

from dataclasses import replace

import pytest

from backend.app.drivers.contracts import AudioMessageResult, ControlNotReady, ControlOperationError
from backend.app.drivers.yoosee import audio, shared_control_reads, shared_talk
from backend.app.drivers.yoosee import audio_capabilities as policy
from backend.app.drivers.yoosee.capability_policy import OperationProof
from backend.app.drivers.yoosee.p2p.talk_inbox import TalkOutcome
from tests import test_audio_capability_routes as audio_fixtures

CAMERA = audio_fixtures.CAMERA
managed = audio_fixtures.managed


def shared_profile(monkeypatch, profile, *, operation=policy.MESSAGE, options=None):
    updated = replace(profile, operations=(OperationProof(operation, writable=True,
        options=frozenset(options or {policy.SHARED})),))
    monkeypatch.setattr(policy.profiles, "load", lambda **_: updated)
    monkeypatch.setattr(shared_control_reads, "has_session", lambda _: True)


def test_separate_shared_message_proof_prepares_dispatch_without_granting_stream(monkeypatch, managed):
    shared_profile(monkeypatch, managed)
    assert policy.allowed_routes(CAMERA, policy.MESSAGE) == {policy.SHARED}
    assert policy.allowed_routes(CAMERA, policy.STREAM) == set()
    assert audio.supported(CAMERA) and not audio.supported(CAMERA, streaming=True)


def test_no_owner_means_no_shared_route_or_independent_login(monkeypatch, managed):
    shared_profile(monkeypatch, managed)
    monkeypatch.setattr(shared_control_reads, "has_session", lambda _: False)
    monkeypatch.setattr(audio, "send_pcm_intercom", lambda *_: pytest.fail("no independent login"))
    with pytest.raises(ControlNotReady):
        audio.send(CAMERA, bytes(640))


def test_selected_shared_message_uses_existing_pcm_adapter_not_rtsp_or_standalone(monkeypatch, managed):
    shared_profile(monkeypatch, managed, options={policy.SHARED, policy.RTSP})
    calls = []
    result = AudioMessageResult(20, 1, 1, 1, False, True, False,
        shared_session=True, microphone_stop_acknowledged=True)
    monkeypatch.setattr(shared_talk, "send_message", lambda entry, pcm: calls.append(pcm) or result)
    monkeypatch.setattr(audio.rtsp_backchannel, "send", lambda *_: pytest.fail("no RTSP replay"))
    monkeypatch.setattr(audio, "send_pcm_intercom", lambda *_: pytest.fail("no standalone route"))
    assert audio.send(CAMERA, bytes(640)) is result and calls == [bytes(640)]
    assert result.completed and result.public()["route_released"] is False


@pytest.mark.parametrize("error", [ControlNotReady("recovering"), ControlOperationError("unconfirmed")])
def test_shared_failure_never_replays_via_other_authorized_route(monkeypatch, managed, error):
    shared_profile(monkeypatch, managed, options={policy.SHARED, policy.RTSP})
    def fail(*_):
        raise error
    monkeypatch.setattr(shared_talk, "send_message", fail)
    monkeypatch.setattr(audio.rtsp_backchannel, "send", lambda *_: pytest.fail("no replay"))
    with pytest.raises(type(error)):
        audio.send(CAMERA, bytes(640))


@pytest.mark.parametrize("stop,expected", [(False, False), (True, True)])
def test_shared_completion_does_not_fabricate_connection_or_route_release(stop, expected):
    result = AudioMessageResult(20, 1, 1, 1, False, True, False,
        shared_session=True, microphone_stop_acknowledged=stop)
    assert result.completed is expected
    assert not result.direct_connection and not result.route_released
    assert result.public()["microphone_stop_acknowledged"] is stop
    legacy = AudioMessageResult(20, 1, 1, 1, True, True, True)
    assert legacy.completed and "shared_session" not in legacy.public()


@pytest.mark.parametrize("outcome", [TalkOutcome("unavailable", 0, False),
    TalkOutcome("transport_received", 1, False), TalkOutcome("cancelled", 1, True)])
def test_partial_outcomes_cannot_be_reported_as_dashboard_success(monkeypatch, outcome):
    monkeypatch.setattr(shared_talk, "send_candidate", lambda *_: outcome)
    with pytest.raises(ControlOperationError):
        shared_talk.send_message(object(), bytes(640))


def test_only_complete_outcome_maps_to_shared_contract(monkeypatch):
    monkeypatch.setattr(shared_talk, "send_candidate", lambda *_: TalkOutcome("transport_received", 1, True))
    result = shared_talk.send_message(object(), bytes(640))
    assert result.completed and result.shared_session and not result.route_released


def test_shared_option_cannot_enable_an_unimplemented_stream(monkeypatch, managed):
    from tests.test_audio_capability_routes import IDENTITY

    shared_profile(monkeypatch, managed, operation=policy.STREAM)
    monkeypatch.setattr(policy.rollout, "selected", lambda _: (IDENTITY, {policy.STREAM}))
    assert policy.allowed_routes(CAMERA, policy.STREAM) == set()
    assert not audio.supported(CAMERA, streaming=True)
    def chunks():
        pytest.fail("unimplemented shared stream cannot consume microphone input")
        yield b""
    with pytest.raises(ControlNotReady):
        audio.send_stream(CAMERA, chunks())
