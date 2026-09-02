from backend.app.drivers.yoosee.p2p.onboard_storage import (
    OnboardStorageState,
    can_advertise_onboard_recordings,
    extract_onboard_storage_state,
)


def test_extracts_absent_card_as_valid_but_not_present_state():
    state = extract_onboard_storage_state({"total": 0, "remain": 0, "stat": 0, "cid": 0})

    assert state == OnboardStorageState(0, 0, 0, 0)
    assert state.present is False


def test_extracts_known_wrapped_tf_info_without_guessing_capacity_units():
    state = extract_onboard_storage_state(
        {"ProReadonly": {"tfInfo": {"total": 31_000, "remain": 20_000, "stat": 7}}}
    )

    assert state == OnboardStorageState(31_000, 20_000, 7)
    assert state.present is True


def test_rejects_malformed_or_inconsistent_tf_info():
    assert extract_onboard_storage_state(None) is None
    assert extract_onboard_storage_state({"total": True, "remain": 0, "stat": 0}) is None
    assert extract_onboard_storage_state({"total": 10, "remain": 11, "stat": 1}) is None
    assert extract_onboard_storage_state({"total": 10, "remain": 1, "stat": -1}) is None
    assert extract_onboard_storage_state({"total": 10, "remain": 1, "stat": 1, "cid": []}) is None


def test_capability_gate_has_no_optimistic_status_or_family_default():
    state = OnboardStorageState(31_000, 20_000, 7)

    assert can_advertise_onboard_recordings(state, readable_statuses=frozenset()) is False
    assert (
        can_advertise_onboard_recordings(
            state,
            readable_statuses=frozenset({7}),
            profile_verified=True,
        )
        is True
    )


def test_successful_read_only_probe_can_verify_an_exact_camera():
    state = OnboardStorageState(31_000, 20_000, 7)

    assert (
        can_advertise_onboard_recordings(
            state,
            readable_statuses=frozenset({7}),
            playback_probe_verified=True,
        )
        is True
    )
    assert (
        can_advertise_onboard_recordings(
            OnboardStorageState(0, 0, 7),
            readable_statuses=frozenset({7}),
            playback_probe_verified=True,
        )
        is False
    )
