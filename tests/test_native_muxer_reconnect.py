"""Real local MP4 rejection, simulated retired account, no camera or decoder process."""

from types import SimpleNamespace

import pytest

from backend.app.drivers.yoosee.native_recovery import retryable
from backend.app.drivers.yoosee.native_video_source import NativeVideoResult, NativeVideoSource
from backend.app.drivers.yoosee.p2p.account_read_worker import WorkerSummary
from backend.app.drivers.yoosee.shared_read_session import SharedReadResult
from backend.app.media.fragmented_mp4 import FragmentedMp4
from backend.app.media.recovering_source import RecoveringSource
from tests.test_fragmented_mp4 import box
from tests.test_recovering_source import clock as clock


@pytest.mark.parametrize('fault', ['trailer', 'capacity'])
def test_retired_failed_muxer_reconnects_without_restarting_app_or_reusing_decoder(clock, fault):
    retired = []
    second_ran = []
    worker = WorkerSummary('stopped', 0, 0, 0, False,
        media=SimpleNamespace(opened=True, broker_release=True),
        tcp=SimpleNamespace(ready=True, close_acknowledged=False, socket_close_failed=False))
    class Epoch(NativeVideoSource):
        def __init__(self, failing):
            super().__init__('test', '123')
            self.failing = failing
        def run(self, *, cancelled):
            output = FragmentedMp4()
            try:
                if self.failing:
                    if fault == 'trailer':
                        output.feed(box(b'ftyp') + box(b'moov') + box(b'mfra'))
                    else:
                        from backend.app.media.fragmented_mp4 import MAX_BYTES
                        output.feed(bytes(MAX_BYTES + 1))
                output.feed(box(b'ftyp') + box(b'moov') + box(b'moof') + box(b'mdat'))
                assert output.take().initialization
                assert not output.take().initialization
                second_ran.append(True)
                return NativeVideoResult('stopped', SharedReadResult('stopped', worker))
            except ValueError as exc:
                return NativeVideoResult('consumer_failed', SharedReadResult('stopped', worker), type(exc).__name__)
            finally:
                output.close()
                retired.append(self)
    first = Epoch(True)
    def construct():
        assert retired == [first] and clock[0] >= 2
        return Epoch(False)
    source = RecoveringSource(first, factory=construct, valid=lambda: True, retryable=retryable,
                              clock=lambda: clock[0])
    result = source.run(cancelled=lambda: bool(second_ran))
    assert result.reason == 'stopped' and source.restarts == 1 and len(retired) == 2
