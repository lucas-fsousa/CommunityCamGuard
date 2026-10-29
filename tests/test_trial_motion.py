"""The operator must explicitly scope motion; no network or production mutation."""

from dataclasses import replace

import pytest

from backend.app.diagnostics.trial_motion import motion_grants, write_allowed
from tests.test_read_preparation import ENROLLMENT


def test_default_trial_has_no_motion_grant():
    assert motion_grants(ENROLLMENT, frozenset()) == ()
    assert not write_allowed(f"/api/cameras/{ENROLLMENT.camera_id}/ptz",
                             camera_id=ENROLLMENT.camera_id, motion=False)


def test_explicit_scope_retains_exact_enrollment_and_directions():
    grant, = motion_grants(ENROLLMENT, frozenset({"left", "right"}))
    assert grant.device_id == int(ENROLLMENT.device_id)
    assert grant.access_id == ENROLLMENT.access_id
    assert grant.directions == frozenset({"left", "right"})


@pytest.mark.parametrize("directions", [{"right"}, frozenset({"other"})])
def test_invalid_direction_scope_is_rejected(directions):
    with pytest.raises(ValueError):
        motion_grants(ENROLLMENT, directions)


def test_invalid_access_is_rejected_before_trial():
    with pytest.raises(ValueError):
        motion_grants(replace(ENROLLMENT, access_id=0), frozenset({"right"}))


@pytest.mark.parametrize("path,allowed", [
    ("/api/login", True), ("/api/logout", True), ("/api/media/client-event", True),
    (f"/api/cameras/{ENROLLMENT.camera_id}/ptz", True),
    ("/api/cameras/cam_" + "b" * 24 + "/ptz", False),
    (f"/api/cameras/{ENROLLMENT.camera_id}/reboot", False),
    (f"/api/cameras/{ENROLLMENT.camera_id}/controls/floodlight", False),
    (f"/api/cameras/{ENROLLMENT.camera_id}/ptz/", False),
    ("/api/cameras", False),
])
def test_motion_trial_only_opens_target_ptz_route(path, allowed):
    assert write_allowed(path, camera_id=ENROLLMENT.camera_id, motion=True) is allowed
