"""Expired decoder + late lease retirement must not strand the source registry."""

from backend.app.drivers.yoosee.native_lease_recovery import retryable
from backend.app.drivers.yoosee.native_video_source import NativeVideoResult, NativeVideoSource
from backend.app.drivers.yoosee.p2p.account_media_channel import MediaChannelSummary
from backend.app.drivers.yoosee.p2p.cooperative_udp_media import UdpMediaResult
from backend.app.drivers.yoosee.p2p.media_route_job import MediaRouteResult
from backend.app.drivers.yoosee.shared_read_session import SharedReadResult
from backend.app.media import recovering_source
from backend.app.media.source_registry import SourceRegistry
from tests.test_leased_read_session import setup


def test_late_owner_close_reaches_retry_and_new_source_without_container_or_registry_reset(monkeypatch):
    session, owner, inbox, now = setup()
    camera_id = session._entry.camera_id
    first = NativeVideoSource(camera_id, '100', shared_session=session)
    second = NativeVideoSource(camera_id, '100')
    registry, claimed, replacements = SourceRegistry(), [], []

    def wait(_seconds):
        now[0] += 1
        assert registry.state(camera_id) == 'running'
        assert not replacements
        if not claimed:
            claimed.append(inbox.claim()[0])
        if now[0] == 8:
            assert inbox.account_retirement_requested
            owner.active = False
            inbox.finish(claimed[0], MediaChannelSummary(100,
                MediaRouteResult(True, True, False), None, 'protocol',
                udp=UdpMediaResult(True, False, True, 1, 1, 1)), account_retired=True)

    session._wait = wait

    def failed_decoder(*, cancelled):
        result = session.run(video_delivery=first._delivery, cancelled=lambda: now[0] >= 1)
        return NativeVideoResult('consumer_failed', result, 'TimeoutError')

    def replacement():
        assert not owner.active and now[0] >= 10
        assert first.diagnostics()['session_lease_detached'] is True
        replacements.append(True)
        return second

    monkeypatch.setattr(first, 'run', failed_decoder)
    monkeypatch.setattr(second, 'run', lambda **_: NativeVideoResult(
        'enrollment_changed', SharedReadResult('enrollment_changed')))
    monkeypatch.setattr(recovering_source.time, 'sleep', lambda duration: now.__setitem__(0, now[0]+duration))
    wrapper = recovering_source.RecoveringSource(first, factory=replacement, valid=lambda: True,
        retryable=retryable, clock=lambda: now[0])
    result = registry.run(camera_id, domain='synthetic-native', factory=lambda: wrapper, cancelled=lambda: False)
    assert result.reason == 'enrollment_changed' and replacements == [True]
    assert wrapper.restarts == 1 and registry.state(camera_id) == 'idle'
