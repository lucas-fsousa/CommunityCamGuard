"""Full driver-plan → startup → three consumers, one account. No real camera I/O."""

import time
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from _native_fixture import FRAGMENT, INIT
from pydantic import ValidationError

from backend.app import drivers
from backend.app.config import Settings
from backend.app.db import p2p, registry
from backend.app.drivers.yoosee import (
    capability_profiles,
    capability_rollout,
    capability_snapshot_store,
    native_account,
    native_media_offer,
    native_video_source,
    shared_control_reads,
)
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_policy import OperationProof, ValidatedProfile
from backend.app.drivers.yoosee.capability_snapshot import CapabilitySnapshot
from backend.app.drivers.yoosee.driver import YooseeDriver
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import ReadConnection
from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from backend.app.media.native_runtime import NativeSourceRuntime
from backend.app.media.server_owner import ServerOwner
from backend.app.services.native_startup import restore_reviewed
from tests.test_account_media_route import MediaSocket
from tests.test_broker_reads import DEVICE, NODE
from tests.test_captured_media import header
from tests.test_hevc_recovery import idr
from tests.test_media_lease_worker import Tcp
from tests.test_native_multi_restart import checkpoints
from tests.test_native_restart import coordinator


@pytest.fixture
def reviewed(monkeypatch):
    cameras, pending = checkpoints(("yoosee/shared-control",) * 3)
    entries = []
    for index, camera in enumerate(cameras):
        identity = CapabilityIdentity(str(7000000001 + index), "123", "IPC", 1, "firmware", "sdk", "")
        entries.append(p2p.upsert_enrollment(identity.device_id, access_id=123, access_token=b"x" * 64,
                                             camera_id=camera.camera_id))
        operations = (OperationProof("native_video", readable=True, options=frozenset({"hevc"})),)
        if index < 2:
            operations += (OperationProof("native_media_ptz", writable=True,
                options=frozenset({"right" if index == 0 else "left"})),)
        profile = ValidatedProfile(camera.camera_id, identity, operations)
        capability_profiles.register(profile, sources={item.key: "sha256:" + "a" * 64 + " proof#group"
                                                       for item in operations}, reviewed_at=100)
        capability_rollout.activate(profile)
        generation = capability_snapshot_store.begin(camera.camera_id)
        capability_snapshot_store.save(CapabilitySnapshot(camera.camera_id, identity, 100, ()),
                                        generation=generation, expires_at=200)
    monkeypatch.setattr(native_media_offer, "time", lambda: 150)
    return cameras, entries, pending


def test_ordinary_startup_uses_driver_group_and_one_login_for_three_native_consumers(reviewed, monkeypatch, tmp_path):
    cameras, entries, _ = reviewed
    legacy = registry.upsert_camera("aa:bb:cc:dd:ee:10")
    sock, events, decoders, pumps = MediaSocket(), [], [], []
    targets = tuple(replace(DEVICE, device_id=int(entry.device_id)) for entry in entries)
    receive = sock.recvfrom
    def paced_receive(size):
        time.sleep(0.001)
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", paced_receive)
    @contextmanager
    def handoff(**kwargs):
        events.append("sdk_enter")
        try:
            yield
        finally:
            events.append("sdk_exit")
    def connect(entry, **kwargs):
        events.append("login")
        assert {entry.device_id, *kwargs["additional_device_ids"]} == {item.device_id for item in entries}
        target = next(item for item in targets if item.device_id == int(entry.device_id))
        return ReadConnection(NODE, sock, "192.0.2.1", 1234, target, 123, targets)
    class Decoder:
        pending_bytes = 0
        def __init__(self, *, output, frame_rate, live_timing):
            self.output, self.closed = output, False
            decoders.append(self)
        def offer(self, payload, *, timestamp=None):
            self.output.feed(INIT + FRAGMENT)
            return True
        def step(self): pass
        def close(self):
            self.closed = True
            self.output.close()
    def opener(route, spec, *, clock, video, policy):
        video.consume(V1Record(encoding=unpack_v1_encoding_header(header()), video=idr(), video_timestamp=1))
        pump = Tcp(video)
        pumps.append(pump)
        return pump
    monkeypatch.setattr(native_account, "reserve_read_handoff", handoff)
    monkeypatch.setattr(native_account, "open_read_connection", connect)
    monkeypatch.setattr(native_account, "control_channel_contended", lambda: False)
    monkeypatch.setattr(native_video_source, "HevcDecoder", Decoder)
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.account_read_worker.open_tcp_media", opener)
    server = ServerOwner(tmp_path / "owner.db")
    server.acquire()
    driver = YooseeDriver()
    service, = driver.runtime_services(server)
    def for_camera(camera):
        assert camera.camera_id != legacy.camera_id  # No planning/migration of RTSP cameras.
        return driver
    monkeypatch.setattr(drivers, "for_camera", for_camera)
    retirement = coordinator()
    runtime = NativeSourceRuntime(viewers=retirement.viewer_gate, capacity=3, restart_checkpoints=True)
    account = None
    try:
        statuses = restore_reviewed(runtime, retirement, SimpleNamespace(native_archive=lambda _: None), lambda: None)
        assert statuses == {camera.camera_id: "running" for camera in cameras}
        assert len(decoders) == len(pumps) == len(shared_control_reads._sessions) == 3
        assert events == ["sdk_enter", "login"] and sock.closed == 0
        account = service._account
        for entry in entries:
            binding = account.binding(entry.camera_id)
            assert all(grant.device_id == int(entry.device_id) for grant in binding._motion_grants)
        selected = cameras[0].camera_id
        assert runtime.sources.stop(selected)
        runtime._activations[selected].session.completion.result(timeout=3)
        assert runtime.sources.state(selected) == "idle" and account.ready and sock.closed == 0
        assert len(shared_control_reads._sessions) == 2
        assert events.count("login") == 1
    finally:
        runtime.stop(timeout=6)
        service.stop()
        server.close()
    assert account is not None and account.cleanup_confirmed
    assert events == ["sdk_enter", "login", "sdk_exit"]
    assert sock.opens == sock.closes == 3 and sock.closed == 1
    assert all(item.closed for item in decoders) and not shared_control_reads._sessions


@pytest.mark.parametrize("fault", ["account", "evidence", "duplicate", "unowned"])
def test_driver_group_refuses_unreviewed_or_conflicting_members_without_a_login(reviewed, monkeypatch, tmp_path, fault):
    cameras, entries, _ = reviewed
    server = ServerOwner(tmp_path / "owner.db")
    server.acquire()
    driver = YooseeDriver()
    service, = driver.runtime_services(server)
    if fault == "account":
        p2p.upsert_enrollment(entries[1].device_id, access_id=999, access_token=b"x" * 64,
                              camera_id=entries[1].camera_id)
    elif fault == "evidence":
        capability_snapshot_store.begin(cameras[1].camera_id)
    elif fault == "duplicate":
        cameras = [cameras[0], cameras[0]]
    elif fault == "unowned":
        driver = YooseeDriver()
    try:
        assert driver.native_media_offers(tuple(cameras), archives={}) == ()
        assert service._account is None and service._thread is None
    finally:
        service.stop()
        server.close()


@pytest.mark.parametrize("capacity", [0, 33, -1])
def test_native_source_capacity_is_bounded_configuration(capacity):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, native_source_capacity=capacity)


def test_group_plan_is_lazy_exact_and_revalidated_before_construction(reviewed, tmp_path):
    cameras, _, _ = reviewed
    server = ServerOwner(tmp_path / "owner.db")
    server.acquire()
    driver = YooseeDriver()
    service, = driver.runtime_services(server)
    try:
        offered = driver.native_media_offers(tuple(reversed(cameras)), archives={})
        assert len(offered) == 3 and len({item.domain for item in offered}) == 3
        assert all(item.accepts_restart_domain("yoosee/shared-control") for item in offered)
        assert service._account is None and service._thread is None
        sources = [item.factory().current for item in offered]
        assert len({id(source._cohort) for source in sources}) == 1
        assert service._account is None and service._thread is None
        singleton, = driver.native_media_offers((cameras[0],), archives={})
        assert singleton.accepts_restart_domain("yoosee/media-lease/" + cameras[0].camera_id)
        assert not singleton.accepts_restart_domain("yoosee/media-lease/" + cameras[1].camera_id)
        capability_snapshot_store.begin(cameras[1].camera_id)
        assert all(not item.valid() for item in offered)
    finally:
        service.stop()
        server.close()
