"""Deterministic motion-owner tests; no camera, sockets or sleeping worker threads."""
import pytest

from backend.app.drivers.yoosee.p2p.ptz_motion import PtzBusy, PtzMotion, PtzOwners


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class StopEvent:
    def __init__(self, clock):
        self.clock = clock
        self.stopped = False

    def set(self):
        self.stopped = True

    def is_set(self):
        return self.stopped

    def wait(self, timeout):
        if not self.stopped:
            self.clock.now += timeout


class Route:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.start_error = False
        self.release_error = False
        self.close_error = False
        self.confirmed = True
        self.on_start = lambda: None

    def send_start(self):
        self.calls.append(("start", self.clock()))
        self.on_start()
        if self.start_error:
            raise OSError("secret/session data must not be returned")

    def send_release(self):
        self.calls.append(("release", self.clock()))
        if self.release_error:
            raise OSError("delivery unknown")

    def confirm_release(self, *, deadline):
        self.calls.append(("confirm", deadline))
        if not self.confirmed:
            self.clock.now = deadline
        return self.confirmed

    def close(self):
        self.calls.append(("close", self.clock()))
        if self.close_error:
            raise OSError("close failed")


def fixture(duration=0.2):
    clock = Clock()
    motion = PtzMotion(duration, clock=clock)
    motion._stop = StopEvent(clock)
    return motion, Route(clock)


def test_release_deadline_does_not_wait_for_start_ack():
    motion, route = fixture()
    result = motion.run(route)
    assert route.calls == [("start", 0), ("release", 0.2), ("confirm", 0.7), ("close", 0.2)]
    assert result.start_attempted and result.release_delivery_confirmed
    assert not result.fallback_allowed
    assert result.error_types == ()


def test_explicit_stop_shortens_lease_without_second_start():
    motion, route = fixture()
    route.on_start = motion.stop
    result = motion.run(route)
    assert result.cancelled
    assert route.calls[1] == ("release", 0)
    assert not result.fallback_allowed


def test_cancel_before_run_never_sends_motion_or_allows_fallback():
    motion, route = fixture()
    motion.stop()
    result = motion.run(route)
    assert route.calls == [("close", 0)]
    assert not result.start_attempted
    assert result.cancelled and not result.fallback_allowed


def test_ambiguous_start_failure_still_releases_and_forbids_fallback():
    motion, route = fixture()
    route.start_error = True
    result = motion.run(route)
    assert result.start_attempted and result.release_delivery_confirmed
    assert not result.fallback_allowed
    assert result.error_types == ("OSError",)
    assert route.calls[1] == ("release", 0)
    assert sum(name == "start" for name, _ in route.calls) == 1


@pytest.mark.parametrize("send_failure", [False, True])
def test_release_failure_is_bounded_and_never_restarts_motion(send_failure):
    motion, route = fixture()
    route.release_error = send_failure
    route.confirmed = False
    result = motion.run(route)
    assert result.release_attempts == 3
    assert not result.release_delivery_confirmed and not result.fallback_allowed
    assert sum(name == "start" for name, _ in route.calls) == 1
    assert route.calls[-1][0] == "close"
    assert route.clock.now <= 2.2


def test_same_owner_cannot_be_reused_or_reentered():
    motion, route = fixture()

    def reenter():
        with pytest.raises(PtzBusy):
            motion.run(route)

    route.on_start = reenter
    motion.run(route)
    with pytest.raises(PtzBusy):
        motion.run(route)
    assert sum(name == "start" for name, _ in route.calls) == 1


def test_close_failure_does_not_authorize_fallback():
    motion, route = fixture()
    route.close_error = True
    result = motion.run(route)
    assert result.error_types == ("OSError",)
    assert not result.fallback_allowed


@pytest.mark.parametrize("duration", [True, None, 0, 0.09, 0.51, 8, float("nan"), float("inf")])
def test_invalid_lease_is_rejected_before_route_use(duration):
    with pytest.raises(ValueError):
        PtzMotion(duration)


def test_owners_are_bounded_per_camera_and_release_after_failure():
    owners = PtzOwners(limit=2)
    with owners.reserve("camera-a"):
        with pytest.raises(PtzBusy):
            with owners.reserve("camera-a"):
                pytest.fail("duplicate owner")
        with owners.reserve("camera-b"):
            with pytest.raises(PtzBusy):
                with owners.reserve("camera-c"):
                    pytest.fail("unbounded workers")
    with pytest.raises(OSError):
        with owners.reserve("camera-a"):
            raise OSError("prepare failed")
    with owners.reserve("camera-a"):
        pass


@pytest.mark.parametrize("limit", [True, 0, 9, -1])
def test_invalid_owner_capacity(limit):
    with pytest.raises(ValueError):
        PtzOwners(limit=limit)
