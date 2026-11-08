"""Prepared owner + recovering source, virtual time and no camera operations."""

import pytest
from _native_runtime_fixture import retired_native_transition

from backend.app.media import recovering_source
from backend.app.media.prepared_native import PreparedNative
from backend.app.media.source_offer import NativeSourceOffer
from backend.app.media.source_registry import SourceRegistry, SourceValidationBusy


@pytest.mark.parametrize('failure', ['busy', 'revoked', 'error', 'cancel'])
def test_active_validation_pause_does_not_latch_owner_stop_but_revocation_does(monkeypatch, failure):
    camera, _, receipt, viewers, _ = retired_native_transition()
    now, state, events = [0.0], {'failed': False}, []
    def valid():
        if state['failed']:
            if failure in {'busy', 'cancel'}:
                raise SourceValidationBusy('metadata unavailable')
            if failure == 'error':
                raise OSError('unknown validation failure')
            return False
        return True
    offer = NativeSourceOffer(camera.camera_id, 'test/validation', lambda: None, valid)
    session = PreparedNative(offer=offer, selection=receipt.selection,
        sources=SourceRegistry(), viewers=viewers, clock=lambda: now[0])
    class Child:
        def __init__(self, first): self.first = first
        def run(self, *, cancelled):
            events.append('first' if self.first else 'replacement')
            if self.first:
                state['failed'], now[0] = True, 1.0
                assert cancelled()  # Retire instead of continuing on unknown grants.
                events.append('retired')
                return 'stopped'
            assert not state['failed'] and not cancelled()
            return 'finished'
    source = recovering_source.RecoveringSource(Child(True), factory=lambda: Child(False),
        valid=valid, retryable=lambda result, _: result == 'stopped', clock=lambda: now[0])
    session.source = source
    def sleep(duration):
        assert events == ['first', 'retired']  # No new child while evidence is unavailable.
        now[0] += duration
        if failure == 'cancel': session.request_stop()
        state['failed'] = False
    monkeypatch.setattr(recovering_source.time, 'sleep', sleep)
    result = source.run(cancelled=session.cancelled)
    if failure == 'busy':
        assert result == 'finished' and events == ['first', 'retired', 'replacement']
        assert not session._stop.is_set()
    else:
        assert result == 'stopped' and events == ['first', 'retired']
        assert session._stop.is_set()
