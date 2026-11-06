"""Consumer integration with synthetic broker/media and a fake decoder only."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from backend.app.drivers.yoosee import native_video_source as module
from backend.app.drivers.yoosee.leased_read_session import LeasedReadSession
from backend.app.drivers.yoosee.p2p.account_policy import AccountPolicy
from backend.app.drivers.yoosee.p2p.account_read_lifecycle import (
    AccountReadLifecycle,
    ReadConnection,
)
from backend.app.drivers.yoosee.p2p.media_lease_inbox import MediaLeaseInbox
from backend.app.drivers.yoosee.p2p.stream_protocol import unpack_v1_encoding_header
from backend.app.drivers.yoosee.p2p.v1_receive import V1Record
from tests.test_account_media_route import MediaSocket
from tests.test_av_route import ENROLLMENT
from tests.test_broker_reads import NODE
from tests.test_captured_media import header
from tests.test_fragmented_mp4 import box
from tests.test_hevc_recovery import idr
from tests.test_media_lease_inbox import TARGETS, bootstrap, camera
from tests.test_media_lease_worker import Tcp


def test_native_consumer_borrows_one_channel_and_does_not_stop_sibling(monkeypatch):
    sock, stop, offered = MediaSocket(), threading.Event(), threading.Event()
    lifecycle = AccountReadLifecycle(policy=AccountPolicy.CONTINUOUS, clock=lambda: sock.now)
    boxes, pumps, decoded = [], [], []
    receive = sock.recvfrom
    def paced_receive(size):
        time.sleep(0.0005)
        return receive(size)
    monkeypatch.setattr(sock, "recvfrom", paced_receive)
    def leases(connection, generation):
        inbox = MediaLeaseInbox(generation, 123, targets=connection.targets)
        boxes.append(inbox)
        inbox.submit(camera(1), bootstrap(1))
        return inbox
    def opener(route, spec, *, clock, video, policy):
        video.consume(V1Record(encoding=unpack_v1_encoding_header(header()),
                              video=idr(), video_timestamp=1))
        pump = Tcp(video)
        pumps.append((spec.device.device_id, pump))
        return pump
    monkeypatch.setattr("backend.app.drivers.yoosee.p2p.account_read_worker.open_tcp_media", opener)
    class Decoder:
        pending_bytes = 0
        def __init__(self, *, output, frame_rate, live_timing):
            self.output, self.closed = output, False
            decoded.append(self)
        def offer(self, payload, *, timestamp=None):
            self.output.feed(box(b"ftyp") + box(b"moov") + box(b"moof") + box(b"mdat"))
            offered.set()
            return True
        def step(self): pass
        def close(self):
            self.closed = True
            self.output.close()
    monkeypatch.setattr(module, "HevcDecoder", Decoder)
    with ThreadPoolExecutor(max_workers=2) as pool:
        account = pool.submit(lifecycle.run_media_leases,
            lambda _: ReadConnection(NODE, sock, "192.0.2.1", 1234, TARGETS[0], 123, TARGETS),
            leases=leases, cancelled=stop.is_set)
        source = None
        try:
            deadline = time.monotonic() + 2
            while not boxes or not lifecycle.owns_media_leases(boxes[0]):
                assert time.monotonic() < deadline
                time.sleep(0.001)
            entry = replace(ENROLLMENT, camera_id=camera(0), device_id="100", access_id=123)
            session = LeasedReadSession(entry, TARGETS[0], lifecycle, boxes[0], current_enrollment=lambda: entry)
            source = module.NativeVideoSource(camera(0), "100", shared_session=session)
            future = pool.submit(source.run, cancelled=lambda: False)
            assert offered.wait(2)
            source.stop()
            result = future.result(timeout=3)
            assert result.reason == "stopped"
            assert source.diagnostics()["session_lease_detached"]
            assert not source.diagnostics()["session_broker_socket_closed"]
            sibling = next(pump for device, pump in pumps if device == 101)
            count = sibling.steps
            time.sleep(0.02)
            assert sibling.steps > count and sibling.result is None
            assert lifecycle.state == "active" and sock.closed == 0
            assert len(decoded) == 1 and decoded[0].closed
            assert not source._thread.is_alive()
        finally:
            if source is not None:
                source.stop()
            stop.set()
            assert account.result(timeout=3).reason == "stopped"
    assert sock.opens == sock.closes == 2 and sock.closed == 1
