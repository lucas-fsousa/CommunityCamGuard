"""Startup rollback and fail-closed server ownership; no services or cameras."""

from unittest.mock import Mock

import pytest

from backend.app.media.server_owner import ServerOwner, ServerOwnerBusy
from backend.app.media.source_registry import SourceUnavailable
from backend.app.services.lifetime import ServiceLifetime


def test_reverse_cleanup_all_attempted_and_failed_owner_is_retained_for_retry(tmp_path):
    owner = ServerOwner(tmp_path / "app.db")
    owner.acquire()
    lifetime = ServiceLifetime(owner)
    events = []
    first = lifetime.own(Mock())
    last = lifetime.own(Mock())
    first.stop.side_effect = lambda: events.append("first")
    def fail():
        events.append("last")
        raise RuntimeError("synthetic cleanup failure")
    last.stop.side_effect = fail
    try:
        with pytest.raises(RuntimeError):
            lifetime.stop()
        assert events == ["last", "first"] and owner.acquired
        assert len(lifetime._services) == 2
        with pytest.raises(ServerOwnerBusy):
            ServerOwner(tmp_path / "app.db").acquire()
        with pytest.raises(SourceUnavailable):
            lifetime.own(Mock())
        last.stop.side_effect = lambda: events.append("retry last")
        lifetime.stop()
        assert not owner.acquired and not lifetime._services
        lifetime.stop()
    finally:
        owner.close()  # no real resources in this fixture


def test_an_unacquired_owner_cannot_register_services(tmp_path):
    with pytest.raises(SourceUnavailable):
        ServiceLifetime(ServerOwner(tmp_path / "app.db"))
