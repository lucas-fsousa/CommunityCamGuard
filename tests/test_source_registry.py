"""Bounded admission without cameras, codecs, external services or subprocesses."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.media.source_registry import (
    SourceCleanupUnconfirmed,
    SourceRegistry,
    SourceUnavailable,
)

CAMERA = "cam_" + "a" * 24
OTHER = "cam_" + "b" * 24


class Source:
    def __init__(self):
        self.entered = threading.Event()
        self.cleanup = threading.Event()
        self.release = threading.Event()
        self.reader = object()

    def run(self, *, cancelled):
        self.entered.set()
        for _ in range(1000):
            if cancelled():
                break
            self.release.wait(0.002)
        else:
            raise RuntimeError("test cancellation not observed")
        self.cleanup.set()
        assert self.release.wait(2)
        return "confirmed"

    def subscribe(self):
        return self.reader


def run(registry, source, camera=CAMERA, domain="driver/shared"):
    return registry.run(camera, domain=domain, factory=lambda: source, cancelled=lambda: False)


def test_duplicate_camera_and_shared_driver_domain_rejected_before_factory():
    registry, source = SourceRegistry(capacity=2), Source()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run, registry, source)
        try:
            assert source.entered.wait(2)
            assert registry.subscribe(CAMERA) is source.reader
            for camera, domain in ((CAMERA, "another-driver"), (OTHER, "driver/shared")):
                with pytest.raises(SourceUnavailable):
                    registry.run(camera, domain=domain, factory=lambda: pytest.fail("extra factory"),
                                 cancelled=lambda: False)
            registry.stop(CAMERA)
            assert source.cleanup.wait(2)
            assert registry.state(CAMERA) == "stopping"
            with pytest.raises(SourceUnavailable):
                registry.subscribe(CAMERA)
            with pytest.raises(SourceUnavailable):
                run(registry, Source(), OTHER)
        finally:
            registry.stop(CAMERA)
            source.release.set()
        assert future.result(timeout=3) == "confirmed"
    assert registry.state(CAMERA) == "idle"
    next_source = Source()
    next_source.release.set()
    assert registry.run(OTHER, domain="driver/shared", factory=lambda: next_source,
                        cancelled=lambda: next_source.entered.is_set()) == "confirmed"


def test_stop_during_factory_latches_cancellation_and_reservation():
    registry = SourceRegistry()
    entered, release = threading.Event(), threading.Event()
    source = Source()
    source.release.set()

    def factory():
        entered.set()
        assert release.wait(2)
        return source

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(registry.run, CAMERA, domain="driver", factory=factory,
                             cancelled=lambda: False)
        try:
            assert entered.wait(2)
            assert registry.state(CAMERA) == "starting"
            assert registry.stop(CAMERA)
            assert registry.state(CAMERA) == "stopping"
        finally:
            registry.stop(CAMERA)
            release.set()
        assert future.result(timeout=3) == "confirmed"
    assert registry.state(CAMERA) == "idle"


@pytest.mark.parametrize("phase", ["factory", "run"])
def test_unknown_failure_blocks_camera_and_domain_without_retry(phase):
    registry = SourceRegistry(capacity=2)

    class FailedSource:
        def run(self, **kwargs):
            raise RuntimeError("unconfirmed private cleanup")

    def factory():
        if phase == "factory":
            raise RuntimeError("unconfirmed private construction")
        return FailedSource()

    with pytest.raises(RuntimeError):
        registry.run(CAMERA, domain="driver", factory=factory, cancelled=lambda: False)
    assert registry.state(CAMERA) == "blocked"
    with pytest.raises(SourceUnavailable):
        run(registry, Source(), OTHER, "driver")
    with pytest.raises(SourceCleanupUnconfirmed):
        registry.stop(CAMERA)


def test_shutdown_signals_independent_domains_and_rejects_future_admission():
    registry = SourceRegistry(capacity=2)
    first, second = Source(), Source()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, registry, first),
                   pool.submit(run, registry, second, OTHER, "other-driver")]
        try:
            assert first.entered.wait(2) and second.entered.wait(2)
            registry.begin_shutdown()
            assert first.cleanup.wait(2) and second.cleanup.wait(2)
            with pytest.raises(SourceUnavailable):
                registry.subscribe(CAMERA)
        finally:
            registry.begin_shutdown()
            first.release.set()
            second.release.set()
        assert all(f.result(timeout=3) == "confirmed" for f in futures)
    assert registry.state(CAMERA) == registry.state(OTHER) == "idle"
    with pytest.raises(SourceUnavailable, match="shutting down"):
        run(registry, Source())


@pytest.mark.parametrize("camera,domain", [("native-123", "driver"), (CAMERA, ""),
                                          (CAMERA, "private\nkey"), (CAMERA, "x" * 129)])
def test_invalid_admission_never_constructs_source(camera, domain):
    with pytest.raises(ValueError):
        SourceRegistry().run(camera, domain=domain, factory=lambda: pytest.fail("factory"),
                             cancelled=lambda: False)


def test_cancelled_admission_has_no_reservation():
    registry = SourceRegistry()
    with pytest.raises(SourceUnavailable, match="cancelled"):
        registry.run(CAMERA, domain="driver", factory=lambda: pytest.fail("factory"),
                     cancelled=lambda: True)
    assert registry.state(CAMERA) == "idle"


def test_capacity_counts_blocked_entries_without_growing_failure_history():
    registry = SourceRegistry()
    with pytest.raises(RuntimeError):
        registry.run(CAMERA, domain="driver", factory=lambda: (_ for _ in ()).throw(RuntimeError()),
                     cancelled=lambda: False)
    with pytest.raises(SourceUnavailable):
        run(registry, Source(), OTHER, "other-driver")
    assert len(registry._entries) == 1


def test_shutdown_still_signals_other_sources_when_one_is_blocked():
    registry, source = SourceRegistry(capacity=2), Source()
    with pytest.raises(RuntimeError):
        registry.run(CAMERA, domain="blocked", factory=lambda: (_ for _ in ()).throw(RuntimeError()),
                     cancelled=lambda: False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run, registry, source, OTHER, "healthy")
        try:
            assert source.entered.wait(2)
            with pytest.raises(SourceCleanupUnconfirmed):
                registry.begin_shutdown()
            assert source.cleanup.wait(2)
        finally:
            registry.stop(OTHER)
            source.release.set()
        assert future.result(timeout=3) == "confirmed"
    assert registry.state(CAMERA) == "blocked" and registry.state(OTHER) == "idle"
