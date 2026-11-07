import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import pytest

from backend.app.drivers.contracts import ControlNotReady
from backend.app.drivers.yoosee import native_account as module
from backend.app.drivers.yoosee import shared_control_reads as publication
from backend.app.drivers.yoosee.p2p.account_read_connect import ReadConnectFailure
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import ReadConnection
from backend.app.drivers.yoosee.p2p.control_ownership import ControlChannelBusy
from backend.app.drivers.yoosee.p2p.video_delivery import VideoDelivery
from tests.test_account_media_route import MediaSocket
from tests.test_av_route import ENROLLMENT
from tests.test_broker_reads import NODE
from tests.test_media_lease_inbox import TARGETS, camera
from tests.test_media_lease_worker import Tcp


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(publication, "_owners", {})
    monkeypatch.setattr(publication, "_sessions", {})
    sock, events, pumps = MediaSocket(), [], []
    entries = tuple(replace(ENROLLMENT, camera_id=camera(index), device_id=str(100 + index), access_id=123)
                    for index in range(3))
    current = {entry.camera_id: entry for entry in entries}
    @contextmanager
    def handoff(**kwargs):
        events.append("sdk_enter")
        try:
            yield
        finally:
            events.append("sdk_exit")
    def connect(entry, **kwargs):
        assert entry is entries[0]
        assert kwargs["additional_device_ids"] == ("101", "102")
        events.append("login")
        return ReadConnection(NODE, sock, "192.0.2.1", 1234, TARGETS[0], 123, TARGETS)
    def opener(route, spec, *, clock, video, policy):
        pump = Tcp(video)
        pumps.append((spec.device.device_id, pump))
        return pump
    receive = sock.recvfrom
    def paced_receive(size):
        time.sleep(0.0005)
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", paced_receive)
    monkeypatch.setattr(module, "reserve_read_handoff", handoff)
    monkeypatch.setattr(module, "open_read_connection", connect)
    monkeypatch.setattr(module, "control_channel_contended", lambda: False)
    monkeypatch.setattr(module, "block_uncertain_channel", lambda: events.append("blocked"))
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.account_read_worker.open_tcp_media", opener)
    account = module.NativeAccount(entries, current_enrollment=current.get, clock=lambda: sock.now)
    return account, entries, current, sock, events, pumps


def wait_ready(account):
    deadline = time.monotonic() + 2
    while not account.ready:
        assert time.monotonic() < deadline
        time.sleep(0.001)


def test_database_busy_before_connect_does_not_poison_an_account_without_a_socket(environment):
    account, _, _, sock, events, _ = environment
    def locked(_):
        error = sqlite3.OperationalError('PRIVATE_DATABASE_DETAIL')
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise error
    account._current = locked
    assert account.run(cancelled=lambda: False).reason == 'busy'
    assert account.cleanup_confirmed and sock.closed == 0
    assert events == ['sdk_enter', 'sdk_exit']  # No login, no uncertain SDK fence.


def test_database_busy_during_media_retires_the_real_owned_lease_before_account_exit(environment):
    account, entries, current, sock, events, pumps = environment
    def read(camera_id):
        if pumps and sock.now >= 1:
            error = sqlite3.OperationalError('PRIVATE_DATABASE_DETAIL')
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise error
        return current.get(camera_id)
    account._current = read
    with ThreadPoolExecutor(max_workers=2) as pool:
        owned = pool.submit(account.run, cancelled=lambda: False)
        try:
            wait_ready(account)
            binding = account.binding(entries[0].camera_id)
            media = pool.submit(binding.run, cancelled=lambda: False, video_delivery=VideoDelivery())
            assert owned.result(timeout=3).reason == 'busy'
            try:
                media.result(timeout=3)
            except sqlite3.OperationalError as error:
                assert error.sqlite_errorcode == sqlite3.SQLITE_BUSY
            assert binding.diagnostics()['session_lease_detached'] is True
        finally:
            account.stop()
            owned.result(timeout=3)
    assert account.cleanup_confirmed and sock.closed == 1
    assert events == ['sdk_enter', 'login', 'sdk_exit']


def test_one_account_login_binds_three_exact_cameras_and_releases_after_owner_exit(environment):
    account, entries, _, sock, events, _ = environment
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(account.run, cancelled=lambda: False)
        try:
            wait_ready(account)
            bindings = [account.binding(entry.camera_id) for entry in entries]
            assert all(binding.matches_target(entry.camera_id, entry.device_id)
                       for entry, binding in zip(entries, bindings, strict=True))
            assert len({id(owner) for owner in publication._owners.values()}) == 1
            assert len(publication._owners) == 3
            with pytest.raises(ControlNotReady):
                account.binding(camera(99))
        finally:
            account.stop()
            assert future.result(timeout=3).reason == "stopped"
    assert events == ["sdk_enter", "login", "sdk_exit"]
    assert account.cleanup_confirmed and sock.closed == 1
    assert not publication._owners
    with pytest.raises(ValueError, match="already"):
        account.run(cancelled=lambda: False)


def test_busy_handoff_releases_logical_members_without_a_login(environment, monkeypatch):
    account, _, _, sock, events, _ = environment
    @contextmanager
    def busy(**kwargs):
        raise ControlChannelBusy("busy")
        yield
    monkeypatch.setattr(module, "reserve_read_handoff", busy)
    assert account.run(cancelled=lambda: False).reason == "busy"
    assert account.cleanup_confirmed and not events and sock.closed == 0
    assert not publication._owners


def test_confirmed_opening_failure_can_release_account_scope(environment, monkeypatch):
    account, _, _, _, events, _ = environment
    def fail(*args, **kwargs):
        raise ReadConnectFailure("synthetic confirmed close")
    monkeypatch.setattr(module, "open_read_connection", fail)
    assert account.run(cancelled=lambda: False).reason == "connection"
    assert account.cleanup_confirmed and events == ["sdk_enter", "sdk_exit"]
    assert not publication._owners


def test_scope_exit_failure_cannot_publish_an_early_cleanup_receipt(environment, monkeypatch):
    account, _, _, _, _, _ = environment
    @contextmanager
    def fail_exit(**kwargs):
        yield
        assert not account.cleanup_confirmed
        raise OSError("synthetic scope retirement")
    monkeypatch.setattr(module, "reserve_read_handoff", fail_exit)
    with pytest.raises(OSError):
        account.run(cancelled=lambda: account.ready)
    assert not account.cleanup_confirmed
    assert len(publication._owners) == 3  # Fenced, not silently reset.


def test_unknown_socket_close_retains_fencing(environment, monkeypatch):
    account, _, _, sock, events, _ = environment
    def fail_close():
        raise OSError("synthetic socket close")
    monkeypatch.setattr(sock, "close", fail_close)
    with pytest.raises(module.P2PProbeError, match="cleanup"):
        account.run(cancelled=lambda: account.ready)
    assert "blocked" in events and not account.cleanup_confirmed
    assert len(publication._owners) == 3


def test_changed_root_credentials_stop_owner_without_a_reauthentication(environment):
    account, entries, current, _, events, _ = environment
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(account.run, cancelled=lambda: False)
        try:
            wait_ready(account)
            current[entries[0].camera_id] = replace(entries[0], updated_at="changed")
            assert future.result(timeout=3).reason == "stopped"
        finally:
            account.stop()
    assert events.count("login") == 1 and account.cleanup_confirmed


def test_group_publication_allows_only_exact_same_owner_members(monkeypatch):
    monkeypatch.setattr(publication, "_owners", {})
    monkeypatch.setattr(publication, "_sessions", {})
    first, second, unknown = object(), object(), object()
    with publication.reserve_native_group((camera(0), camera(1))):
        with publication.publish(camera(0), first), publication.publish(camera(1), second):
            with pytest.raises(ControlNotReady):
                with publication.publish(camera(0), unknown):
                    pass
            with pytest.raises(ControlNotReady):
                with publication.publish(camera(2), unknown):
                    pass
            with pytest.raises(ControlNotReady):
                with publication.reserve_native_owner(camera(2)):
                    pass
        assert not publication._sessions and len(publication._owners) == 2
    assert not publication._owners


def test_three_native_consumers_share_one_server_owned_account_and_replace_one(environment, monkeypatch, tmp_path):
    from backend.app.drivers.yoosee import native_account_service, native_video_source
    from backend.app.drivers.yoosee.native_lease_recovery import wrap
    from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
    from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
    from backend.app.media.server_owner import ServerOwner
    from backend.app.media.source_registry import SourceRegistry
    from backend.app.services.lifetime import ServiceLifetime
    from tests.test_captured_media import header
    from tests.test_fragmented_mp4 import box
    from tests.test_hevc_recovery import idr

    account, entries, _, sock, events, pumps = environment
    monkeypatch.setattr(native_account_service, "NativeAccount", lambda _: account)
    decoded, changed = [], threading.Condition()
    class Decoder:
        pending_bytes = 0
        def __init__(self, *, output, frame_rate, live_timing):
            self.output, self.offered, self.closed = output, False, False
            with changed:
                decoded.append(self)
        def offer(self, payload, *, timestamp=None):
            if getattr(self, "fail_once", False):
                raise OSError("synthetic local decoder failure")
            self.output.feed(box(b"ftyp") + box(b"moov") + box(b"moof") + box(b"mdat"))
            with changed:
                self.offered = True
                changed.notify_all()
            return True
        def step(self): pass
        def close(self):
            self.closed = True
            self.output.close()
    def opener(route, spec, *, clock, video, policy):
        video.consume(V1Record(encoding=unpack_v1_encoding_header(header()), video=idr(), video_timestamp=1))
        pump = Tcp(video)
        pumps.append((spec.device.device_id, pump))
        return pump
    monkeypatch.setattr(native_video_source, "HevcDecoder", Decoder)
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.account_read_worker.open_tcp_media", opener)
    server = ServerOwner(tmp_path / "app.db")
    server.acquire()
    lifetime = ServiceLifetime(server)
    service = lifetime.own(native_account_service.NativeAccounts(server))
    sources, futures = [], []
    registry = SourceRegistry(capacity=3)
    try:
        assert service.prepare(entries) is account
        wait_ready(account)
        with ThreadPoolExecutor(max_workers=3) as pool:
            def start(entry, *, fail_once=False):
                attempts = 0
                def construct():
                    nonlocal attempts
                    source = native_video_source.NativeVideoSource(entry.camera_id, entry.device_id,
                                                                   shared_session=service.binding(entry))
                    if fail_once and attempts == 0:
                        original = source._decoder
                        def fail_decoder(frame):
                            decoder = original(frame)
                            decoder.fail_once = True
                            return decoder
                        source._decoder = fail_decoder
                    attempts += 1
                    sources.append(source)
                    return source
                recovery = wrap(construct(), camera_id=entry.camera_id, factory=construct, valid=lambda: True)
                future = pool.submit(registry.run, entry.camera_id, domain=f"yoosee/media-lease/{entry.camera_id}",
                                     factory=lambda: recovery, cancelled=lambda: False)
                futures.append(future)
                return recovery, future
            try:
                recoveries = [start(entry, fail_once=(index == 0))[0] for index, entry in enumerate(entries)]
                with changed:
                    assert changed.wait_for(lambda: len(decoded) == 4 and sum(child.offered for child in decoded) == 3, 5)
                assert recoveries[0].restarts == 1
                assert any(child.closed and not child.offered for child in decoded)
                assert len(publication._sessions) == 3
                assert registry.stop(entries[0].camera_id)
                assert futures[0].result(timeout=3).reason == "stopped"
                assert registry.state(entries[0].camera_id) == "idle"
                assert all(registry.state(entry.camera_id) == "running" for entry in entries[1:])
                assert len(publication._sessions) == 2 and account.ready and sock.closed == 0
                siblings = [pump for device, pump in pumps if device != 100]
                counts = [pump.steps for pump in siblings]
                _, replacement = start(entries[0])
                with changed:
                    assert changed.wait_for(lambda: len(decoded) == 5 and sum(child.offered for child in decoded) == 4, 2)
                time.sleep(0.02)
                assert all(pump.steps > count and pump.result is None
                           for pump, count in zip(siblings, counts, strict=True))
                assert len(publication._sessions) == 3 and events.count("login") == 1
                assert not replacement.done()
            finally:
                registry.begin_shutdown()
                for source in sources:
                    source.stop()
                for future in futures:
                    future.result(timeout=3)
        assert not publication._sessions and account.ready
        assert all(registry.state(entry.camera_id) == "idle" for entry in entries)
    finally:
        lifetime.stop()
    assert not server.acquired and account.cleanup_confirmed
    assert all(child.closed for child in decoded)
    assert all(not source._thread.is_alive() for source in sources)
    assert sock.opens == sock.closes == 5 and sock.closed == 1
    assert events == ["sdk_enter", "login", "sdk_exit"]
