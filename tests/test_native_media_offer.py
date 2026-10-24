"""Driver-owned native video evidence: local isolated SQLite, no source startup."""

import sqlite3
from dataclasses import replace
from unittest.mock import Mock

import pytest

from backend.app.db import connect, p2p, registry
from backend.app.drivers.base import CameraDriver
from backend.app.drivers.yoosee import (
    YooseeDriver,
    capability_profiles,
    capability_rollout,
    capability_snapshot_store,
    native_media_offer,
)
from backend.app.drivers.yoosee.capability_identity import CapabilityIdentity
from backend.app.drivers.yoosee.capability_policy import OperationProof, ValidatedProfile
from backend.app.drivers.yoosee.capability_snapshot import CapabilitySnapshot
from backend.app.media.source_registry import SourceUnavailable


@pytest.fixture
def evidence(monkeypatch):
    registry.init_db()
    camera = registry.upsert_camera("aa:bb:cc:dd:ee:03")
    identity = CapabilityIdentity("7000000002", "123", "IPC", 1, "firmware", "sdk", "")
    enrollment = p2p.upsert_enrollment(identity.device_id, access_id=12345, access_token=b"x" * 64,
                                      camera_id=camera.camera_id)
    profile = ValidatedProfile(camera.camera_id, identity,
                               (OperationProof("native_video", readable=True, options=frozenset({"hevc"})),))
    capability_profiles.register(profile, sources={"native_video": "sha256:" + "a" * 64 + " proof#native"},
                                 reviewed_at=100)
    capability_rollout.activate(profile)
    generation = capability_snapshot_store.begin(camera.camera_id)
    capability_snapshot_store.save(CapabilitySnapshot(camera.camera_id, identity, 100, ()),
                                    generation=generation, expires_at=200)
    monkeypatch.setattr(native_media_offer, "time", lambda: 150)
    return camera, identity, enrollment, profile


def test_offer_is_lazy_exact_unit_and_does_not_expose_enrollment(evidence, monkeypatch):
    camera, _identity, _enrollment, _profile = evidence
    factory = Mock(return_value=object())
    monkeypatch.setattr(native_media_offer, "NativeVideoSource", Mock(admission_domain="driver/shared", side_effect=factory))
    offer = YooseeDriver().native_media_offer(camera)
    assert offer is not None and offer.valid() and offer.domain == "driver/shared"
    factory.assert_not_called()
    assert "7000000002" not in repr(offer) and "access_token" not in repr(offer)
    offer.factory()
    factory.assert_called_once_with(camera.camera_id, "7000000002")
    assert CameraDriver().native_media_offer(camera) is None


@pytest.mark.parametrize("change", ["snapshot", "expired", "identity", "review", "enrollment", "device", "other-camera"])
def test_missing_or_changed_evidence_never_becomes_brand_wide_support(evidence, change, monkeypatch):
    camera, identity, _enrollment, profile = evidence
    offer = YooseeDriver().native_media_offer(camera)
    assert offer is not None
    if change == "snapshot":
        capability_snapshot_store.begin(camera.camera_id)
    elif change == "expired":
        monkeypatch.setattr(native_media_offer, "time", lambda: 200)
    elif change == "identity":
        capability_rollout.activate(replace(profile, identity=replace(identity, firmware="changed")))
    elif change == "review":
        denied = replace(profile, operations=(OperationProof("orientation", writable=True),))
        capability_rollout.activate(denied)
    elif change == "enrollment":
        p2p.upsert_enrollment(identity.device_id, access_id=12345, access_token=b"y" * 64,
                              camera_id=camera.camera_id)
    elif change == "device":
        p2p.delete_enrollment(identity.device_id)
        p2p.upsert_enrollment("7000000003", access_id=12345, access_token=b"y" * 64,
                              camera_id=camera.camera_id)
    else:
        other = registry.upsert_camera("aa:bb:cc:dd:ee:04", vendor="Yoosee")
        assert YooseeDriver().native_media_offer(other) is None
        return
    assert not offer.valid()
    fresh = YooseeDriver().native_media_offer(camera)
    # Credential rotation invalidates this in-flight offer, not the same unit's
    # reviewed capability. A newly captured credential generation may qualify.
    assert (fresh is not None and fresh.valid()) if change == "enrollment" else fresh is None
    with pytest.raises(SourceUnavailable):
        offer.factory()


def test_repeated_offer_checks_close_evidence_connections_without_waiting_for_gc(evidence, monkeypatch):
    camera, _identity, _enrollment, _profile = evidence
    opened = []
    def tracked():
        connection = connect()
        opened.append(connection)  # intentionally retain refs so GC cannot hide leaks
        return connection
    for module in (capability_profiles, capability_rollout, capability_snapshot_store):
        monkeypatch.setattr(module, "connect", tracked)
    offer = YooseeDriver().native_media_offer(camera)
    assert offer is not None
    for _ in range(10):
        assert offer.valid()
    assert opened
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
