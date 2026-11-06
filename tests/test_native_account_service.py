import threading
from contextlib import suppress

import pytest

from backend.app.drivers.base import CameraDriver
from backend.app.drivers.contracts import ControlNotReady
from backend.app.drivers.yoosee import native_account_service as module
from backend.app.drivers.yoosee.driver import YooseeDriver
from backend.app.media.server_owner import ServerOwner
from backend.app.media.source_registry import SourceCleanupUnconfirmed, SourceUnavailable
from backend.app.services.lifetime import ServiceLifetime


class Account:
    def __init__(self, entries):
        self.entries = entries
        self.cleanup_confirmed = False
        self.started, self.closed = threading.Event(), threading.Event()
        self.fail = False
    def matches(self, entries): return self.entries == entries
    def run(self, *, cancelled):
        self.started.set()
        self.closed.wait(2)
        self.cleanup_confirmed = not self.fail
    def stop(self): self.closed.set()


def test_lazy_service_is_server_owned_and_single_account_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "NativeAccount", Account)
    owner = ServerOwner(tmp_path / "app.db")
    owner.acquire()
    service = module.NativeAccounts(owner)
    try:
        account = service.prepare(("synthetic",))
        assert account.started.wait(1)
        assert service.prepare(("synthetic",)) is account
        with pytest.raises(ControlNotReady):
            service.prepare(("different",))
        service.stop()
        assert service.stopped and account.cleanup_confirmed and owner.acquired
        with pytest.raises(SourceUnavailable):
            service.prepare(("synthetic",))
    finally:
        service.stop()
        owner.close()


def test_uncertain_account_cleanup_keeps_server_lock_and_handles(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "NativeAccount", Account)
    owner = ServerOwner(tmp_path / "app.db")
    owner.acquire()
    lifetime = ServiceLifetime(owner)
    service = lifetime.own(module.NativeAccounts(owner))
    account = service.prepare(("synthetic",))
    assert account.started.wait(1)
    account.fail = True
    try:
        with pytest.raises(SourceCleanupUnconfirmed):
            lifetime.stop()
        assert owner.acquired and service._account is account and not service.stopped
    finally:
        owner.close()  # Synthetic account only; no socket/resources in this fixture.


def test_driver_hook_is_lazy_and_can_rebind_after_a_clean_server_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "NativeAccount", lambda *_: pytest.fail("startup must not authenticate"))
    owner = ServerOwner(tmp_path / "app.db")
    owner.acquire()
    try:
        assert CameraDriver().runtime_services(owner) == ()
        driver = YooseeDriver()
        service, = driver.runtime_services(owner)
        with pytest.raises(SourceUnavailable):
            driver.runtime_services(owner)
        service.stop()
        replacement, = driver.runtime_services(owner)
        replacement.stop()
    finally:
        owner.close()


def test_missing_server_owner_cannot_construct_or_start_driver_service(tmp_path):
    with pytest.raises(SourceUnavailable):
        module.NativeAccounts(ServerOwner(tmp_path / "app.db"))


def test_sources_retire_before_shared_account_and_server_owner(tmp_path, monkeypatch):
    events = []
    class TrackedAccount(Account):
        def stop(self):
            events.append("account")
            super().stop()
    class Source:
        def stop(self):
            assert not account.closed.is_set() and owner.acquired
            events.append("source")
    monkeypatch.setattr(module, "NativeAccount", TrackedAccount)
    owner = ServerOwner(tmp_path / "app.db")
    owner.acquire()
    lifetime = ServiceLifetime(owner)
    service = lifetime.own(module.NativeAccounts(owner))
    account = service.prepare(("synthetic",))
    assert account.started.wait(1)
    lifetime.own(Source())
    try:
        lifetime.stop()
        assert events == ["source", "account"] and not owner.acquired
    finally:
        with suppress(Exception):
            service.stop()
        owner.close()
