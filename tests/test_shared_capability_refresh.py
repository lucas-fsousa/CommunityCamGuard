from dataclasses import replace

import pytest

from backend.app.db.p2p import P2PEnrollment
from backend.app.drivers.yoosee import capability_snapshot_store as store
from backend.app.drivers.yoosee import shared_capability_refresh as shared
from backend.app.drivers.yoosee.capability_evidence import CAPABILITY_PATHS, EvidenceState
from backend.app.drivers.yoosee.p2p.broker_mailbox import BrokerMailbox
from backend.app.drivers.yoosee.p2p.broker_reads import ReadCompletion, ReadRequest
from backend.app.drivers.yoosee.p2p.broker_transactions import ReceiptTicket
from backend.app.drivers.yoosee.p2p.lamp_read_job import LampReadResult

CAMERA = 'cam_' + '1' * 24
ENTRY = P2PEnrollment('7000000002', 1, bytes(64), None, '', '', CAMERA)
VALUES = (
    {'productID': '123', 'productModel': 'model', 'revision': 1},
    {'swVer': '40.1.22', 'sdkVer': 'sdk', 'hwVer': ''},
    {'t': 100, 'setVal': {'nightViewMode': 0, 'multiFlip': 1}},
    {'t': 100, 'setVal': {'enable': 1}},
    {'t': 100, 'setVal': 7},
    {'t': 100, 'setVal': {'supportFunc': 0}},
    {'t': 100, 'stVal': 1},
)


class Session:
    """Actual bounded mailbox, synthetic correlated completions; no sockets/actions."""
    def __init__(self):
        self.mailbox = BrokerMailbox(1, state_reads=True)
        self.paths = []
        self.cancelled = []
        self.active = True
        self.error = None
        self.before_reply = lambda: None
        self.light = LampReadResult('response', False, True)
        self.lamp_cancelled = False

    def matches_enrollment(self, entry):
        return entry == ENTRY and self.active

    def submit(self, path):
        self.paths.append(path)
        ticket = self.mailbox.submit(int(ENTRY.device_id), path)
        work = self.mailbox.poll()
        assert len(work.submissions) == 1
        request = ReadRequest(ReceiptTicket(1, ticket.sequence, 0xB7), int(ENTRY.device_id), path)
        assert self.mailbox.bind(ticket, request)
        self.before_reply()
        if self.error != 'pending':
            if self.error == 'device':
                # Malformed worker result must not be accepted as this device.
                self.bad = ReadCompletion(replace(request, device_id=7), 'response', 0, 0, {})
            else:
                code = 20001 if self.error == 'rejected' else 0
                value = VALUES[(*CAPABILITY_PATHS, 'Action.expelCtrl').index(path)]
                assert self.mailbox.finish(ticket, ReadCompletion(request, 'response', 0, code, value))
        return ticket

    def take(self, ticket):
        if self.error == 'device':
            from backend.app.drivers.yoosee.p2p.broker_mailbox import ReadOutcome
            return ReadOutcome('response', self.bad)
        return self.mailbox.take(ticket)

    def cancel(self, ticket):
        self.cancelled.append(ticket)
        self.mailbox.cancel(ticket)
        self.mailbox.acknowledge_cancel(ticket)

    def submit_lamp_read(self):
        return 'lamp-status-only'

    def take_lamp_read(self, ticket):
        assert ticket == 'lamp-status-only'
        return self.light

    def cancel_lamp_read(self, ticket):
        self.lamp_cancelled = True


@pytest.fixture
def session(monkeypatch):
    result = Session()
    monkeypatch.setattr(shared, 'session_for', lambda entry: result if result.active else None)
    monkeypatch.setattr(shared.p2p, 'read_registered_enrollment', lambda _: ENTRY)
    return result


@pytest.mark.parametrize('siren', [False, True])
def test_read_only_batch_reuses_one_owner_and_normalizes_without_grants(session, siren):
    snapshot = shared.collect(session, ENTRY, include_siren=siren)
    assert snapshot.camera_id == CAMERA
    assert snapshot.identity.device_id == ENTRY.device_id
    assert session.paths == list(CAPABILITY_PATHS + (('Action.expelCtrl',) if siren else ()))
    assert len(session.cancelled) == len(session.paths)
    assert session.mailbox._slots == {}
    assert session.lamp_cancelled
    evidence = {item.feature: item.state for item in snapshot.evidence}
    assert evidence['white_light'] == EvidenceState.SUPPORTED  # OFF still proves readable state.
    assert evidence['orientation'] == EvidenceState.SUPPORTED
    assert ('siren_pulse' in evidence) is siren
    assert not hasattr(snapshot, 'operations')


@pytest.mark.parametrize('failure', ['rejected', 'device', 'owner', 'enrollment', 'deadline'])
def test_failed_or_replaced_batch_is_not_published_and_cancels_waiter(session, monkeypatch, failure):
    if failure in {'rejected', 'device'}:
        session.error = failure
    elif failure == 'owner':
        session.error = 'pending'
        session.before_reply = lambda: setattr(session, 'active', False)
    elif failure == 'enrollment':
        session.before_reply = lambda: monkeypatch.setattr(
            shared.p2p, 'read_registered_enrollment', lambda _: replace(ENTRY, access_id=2))
    else:
        session.error = 'pending'
        ticks = iter([0, 0, 21])
        monkeypatch.setattr(shared.time, 'monotonic', lambda: next(ticks, 21))
    assert shared.collect(session, ENTRY, include_siren=False) is None
    assert len(session.paths) == 1 and len(session.cancelled) == 1
    assert not session.lamp_cancelled
    if failure != 'device':
        assert session.mailbox._slots == {}


def seed(session):
    snapshot = shared.collect(session, ENTRY, include_siren=False)
    assert store.save(snapshot, generation=store.begin(CAMERA), expires_at=snapshot.collected_at + 1)
    return snapshot


def test_refresh_atomically_replaces_without_revoking_media_or_inventing_profile(session):
    previous = seed(session)
    generation = store.refresh_generation(CAMERA)
    def during_read():
        assert store.matches_recorded_identity(camera_id=CAMERA, identity=previous.identity,
                                              now=previous.collected_at + 2)
        assert not store.is_current(camera_id=CAMERA, identity=previous.identity,
                                    now=previous.collected_at + 2)
    session.before_reply = during_read
    assert shared.refresh(ENTRY, validity_seconds=100)
    assert store.refresh_generation(CAMERA) == generation + 1
    assert store.is_current(camera_id=CAMERA, identity=previous.identity, now=previous.collected_at + 2)


def test_failed_refresh_does_not_extend_expired_evidence(session):
    previous = seed(session)
    generation = store.refresh_generation(CAMERA)
    session.error = 'rejected'
    assert not shared.refresh(ENTRY, validity_seconds=100)
    assert store.refresh_generation(CAMERA) == generation
    assert not store.is_current(camera_id=CAMERA, identity=previous.identity, now=previous.collected_at + 2)


def test_explicit_invalidation_during_collection_wins(session):
    previous = seed(session)
    session.before_reply = lambda: store.begin(CAMERA)
    assert not shared.refresh(ENTRY, validity_seconds=100)
    assert store.refresh_generation(CAMERA) is None
    assert not store.matches_recorded_identity(camera_id=CAMERA, identity=previous.identity,
                                              now=previous.collected_at + 2)


@pytest.mark.parametrize('seconds', [True, 0, -1, float('nan'), float('inf'), 86401])
def test_invalid_validity_rejected_before_read(session, seconds):
    with pytest.raises(ValueError):
        shared.refresh(ENTRY, validity_seconds=seconds)
    assert session.paths == []


def test_missing_evidence_or_owner_never_opens_a_new_account(session):
    assert not shared.refresh(ENTRY, validity_seconds=100)
    seed(session)
    session.active = False
    assert not shared.refresh(ENTRY, validity_seconds=100)
