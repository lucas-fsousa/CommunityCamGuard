"""Source ownership orchestration with synthetic frames; no camera or child process."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.drivers.yoosee import native_video_source as module
from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from backend.app.drivers.yoosee.shared_read_session import SharedReadResult
from backend.app.media.mp4_fanout import StreamClosedError
from backend.app.media.source_registry import SourceRegistry, SourceUnavailable
from tests.test_captured_media import header
from tests.test_fragmented_mp4 import box
from tests.test_hevc_recovery import idr


@pytest.fixture
def setup(monkeypatch):
    offered = threading.Event()
    decoders = []
    failure = [None]
    calls = []
    stopped = threading.Event()

    class Session:
        def __init__(self, camera_id, device_id):
            assert camera_id == "test" and device_id == "123"

        def stop(self):
            stopped.set()

        def run(self, *, cancelled, video_delivery):
            calls.append("account")
            try:
                video_delivery.consume(V1Record(encoding=unpack_v1_encoding_header(header()),
                                                video=idr(), video_timestamp=1))
                for _ in range(1500):
                    if cancelled() or stopped.wait(0.002):
                        return SharedReadResult("stopped")
                raise RuntimeError("test source was not stopped")
            finally:
                video_delivery.close()
                calls.append("account_closed")

    class Decoder:
        pending_bytes = 0

        def __init__(self, *, output):
            self.output = output
            self.closed = False
            decoders.append(self)

        def offer(self, payload):
            if failure[0] == "offer":
                raise ValueError("private decoder detail")
            self.output.feed(box(b"ftyp") + box(b"moov") + box(b"moof") + box(b"mdat"))
            offered.set()
            return True

        def step(self):
            pass

        def close(self):
            if failure[0] == "close":
                raise OSError("unknown child shutdown")
            self.closed = True
            self.output.close()

    monkeypatch.setattr(module, "SharedReadSession", Session)
    monkeypatch.setattr(module, "HevcDecoder", Decoder)
    source = module.NativeVideoSource("test", "123")
    return source, offered, decoders, failure, calls


def test_single_source_fans_out_and_joins_owned_consumer_on_stop(setup):
    source, offered, decoders, _, calls = setup
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(source.run, cancelled=lambda: False)
        try:
            assert offered.wait(2)
            # offer precedes bounded publication by a few instructions.
            for _ in range(100):
                try:
                    first = source.subscribe()
                    break
                except StreamClosedError:
                    threading.Event().wait(0.002)
            else:
                pytest.fail("source never became ready")
            second = source.subscribe()
            assert first.take() is second.take()
            assert first.take() is second.take()
        finally:
            source.stop()
        result = future.result(timeout=3)
    assert result.reason == "stopped" and calls == ["account", "account_closed"]
    assert len(decoders) == 1 and decoders[0].closed
    assert not source._thread.is_alive()
    with pytest.raises(StreamClosedError):
        source.subscribe()
    with pytest.raises(ValueError, match="already started"):
        source.run(cancelled=lambda: False)


def test_consumer_failure_cancels_account_and_is_not_retried(setup):
    source, _, decoders, failure, calls = setup
    failure[0] = "offer"
    result = source.run(cancelled=lambda: False)
    assert result.reason == "consumer_failed"
    assert "private" not in repr(result)
    assert calls == ["account", "account_closed"] and len(decoders) == 1
    assert decoders[0].closed and not source._thread.is_alive()


def test_unconfirmed_decoder_shutdown_cannot_report_success(setup):
    source, offered, _, failure, _ = setup
    failure[0] = "close"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(source.run, cancelled=lambda: False)
        try:
            assert offered.wait(2)
        finally:
            source.stop()
        with pytest.raises(module.P2PProbeError, match="shutdown unconfirmed"):
            future.result(timeout=3)
    assert not source._thread.is_alive()


def test_stop_before_run_creates_no_thread_account_or_decoder(setup):
    source, _, decoders, _, calls = setup
    source.stop()
    assert source.run(cancelled=lambda: False).reason == "stopped"
    assert not calls and not decoders and source._thread is None


def test_account_exception_still_retires_consumer(setup, monkeypatch):
    source, _, _, _, _ = setup

    def fail(**kwargs):
        raise module.P2PProbeError("account cleanup unconfirmed")

    monkeypatch.setattr(source._session, "run", fail)
    with pytest.raises(module.P2PProbeError, match="account cleanup"):
        source.run(cancelled=lambda: False)
    assert not source._thread.is_alive() and source._delivery.closed


def test_unknown_thread_shutdown_is_not_treated_as_a_clean_stop(setup, monkeypatch):
    source, _, _, _, _ = setup

    class StuckThread:
        ident = 1
        def __init__(self, **kwargs): pass
        def start(self): pass
        def join(self, timeout): assert timeout == 2
        def is_alive(self): return True

    monkeypatch.setattr(module.threading, "Thread", StuckThread)
    monkeypatch.setattr(source._session, "run", lambda **_: SharedReadResult("stopped"))
    with pytest.raises(module.P2PProbeError, match="consumer shutdown unconfirmed"):
        source.run(cancelled=lambda: False)
    assert source._delivery.closed


def test_cancellation_callback_fault_before_start_retires_queue(setup):
    source, _, decoders, _, calls = setup

    def fail():
        raise RuntimeError("cancel failure")

    with pytest.raises(RuntimeError, match="cancel failure"):
        source.run(cancelled=fail)
    assert source._delivery.closed and not decoders and not calls


def test_registry_drives_native_owner_without_per_reader_sources(setup):
    source, offered, decoders, _, calls = setup
    registry = SourceRegistry()
    camera_id = "cam_" + "a" * 24
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(registry.run, camera_id, domain=source.admission_domain,
                             factory=lambda: source, cancelled=lambda: False)
        try:
            assert offered.wait(2)
            for _ in range(100):
                try:
                    first = registry.subscribe(camera_id)
                    break
                except StreamClosedError:
                    threading.Event().wait(0.002)
            else:
                pytest.fail("source never became ready")
            second = registry.subscribe(camera_id)
            assert first.take() is second.take()
            assert first.take() is second.take()
        finally:
            registry.stop(camera_id)
        assert future.result(timeout=3).reason == "stopped"
    assert registry.state(camera_id) == "idle"
    assert len(decoders) == 1 and decoders[0].closed
    assert calls == ["account", "account_closed"]
    with pytest.raises(StreamClosedError):
        first.take()


def test_native_cleanup_uncertainty_blocks_registry_replacement(setup):
    source, offered, _, failure, _ = setup
    registry = SourceRegistry(capacity=2)
    camera_id = "cam_" + "a" * 24
    failure[0] = "close"
    with pytest.raises(module.P2PProbeError, match="shutdown unconfirmed"):
        registry.run(camera_id, domain=source.admission_domain, factory=lambda: source,
                     cancelled=offered.is_set)
    assert registry.state(camera_id) == "blocked"
    with pytest.raises(SourceUnavailable):
        registry.run("cam_" + "b" * 24, domain=source.admission_domain,
                     factory=lambda: pytest.fail("unsafe replacement"), cancelled=lambda: False)
