from backend.app.drivers.yoosee.p2p.onboard_storage import (
    TF_CARD_ERROR,
    TF_CARD_NORMAL,
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


def test_extracts_real_stval_double_shape_and_normalizes_observed_signed_total_quirk():
    state = extract_onboard_storage_state(
        {
            "stVal": {
                "total": -15_355_872.0,
                "remain": 11_972_800.0,
                "stat": TF_CARD_NORMAL,
                "cid": "sanitized-card-id",
            },
            "t": 1_788_749_700,
        }
    )

    assert state == OnboardStorageState(
        15_355_872,
        11_972_800,
        TF_CARD_NORMAL,
        "sanitized-card-id",
        -15_355_872,
    )
    assert state.present is True


def test_rejects_malformed_or_inconsistent_tf_info():
    assert extract_onboard_storage_state(None) is None
    assert extract_onboard_storage_state({"total": True, "remain": 0, "stat": 0}) is None
    assert extract_onboard_storage_state({"total": 10, "remain": 11, "stat": 1}) is None
    assert extract_onboard_storage_state({"total": 10, "remain": 1, "stat": -1}) is None
    assert extract_onboard_storage_state({"total": 10, "remain": 1, "stat": 1, "cid": []}) is None
    assert extract_onboard_storage_state({"total": 10.5, "remain": 1, "stat": 1}) is None
    assert extract_onboard_storage_state({"total": float("nan"), "remain": 1, "stat": 1}) is None
    assert extract_onboard_storage_state({"total": -10, "remain": 1, "stat": 0}) is None
    assert extract_onboard_storage_state({"total": -10, "remain": 11, "stat": 1}) is None


def test_apk_card_status_constants_do_not_make_an_error_card_readable():
    state = OnboardStorageState(31_000, 20_000, TF_CARD_ERROR)

    assert (
        can_advertise_onboard_recordings(
            state,
            readable_statuses=frozenset({TF_CARD_NORMAL}),
            playback_probe_verified=True,
        )
        is False
    )


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
