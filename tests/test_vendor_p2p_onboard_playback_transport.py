from __future__ import annotations

import pytest

from backend.app.drivers.yoosee.p2p import onboard_playback_session
from backend.app.drivers.yoosee.p2p.contracts import P2PProbeError
from backend.app.drivers.yoosee.p2p.onboard_playback_transport import (
    require_runtime_playback_read_certified,
)


def test_live_playback_transport_fails_closed_until_physically_certified():
    with pytest.raises(P2PProbeError, match="not runtime-certified"):
        require_runtime_playback_read_certified()


def test_public_listing_fails_before_opening_any_socket(monkeypatch):
    def forbidden_socket(*_args, **_kwargs):
        raise AssertionError("playback gate opened a socket")

    monkeypatch.setattr(onboard_playback_session.socket, "socket", forbidden_socket)

    with pytest.raises(P2PProbeError, match="not runtime-certified"):
        onboard_playback_session.list_camera_onboard_recordings(  # type: ignore[arg-type]
            None,
            None,
        )
