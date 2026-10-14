from dataclasses import replace

import pytest

from backend.app.drivers.yoosee.motion_evidence import grant_from_completions
from backend.app.drivers.yoosee.native_ptz_policy import PtzProfile
from backend.app.drivers.yoosee.p2p.broker_reads import ReadCompletion, ReadRequest
from backend.app.drivers.yoosee.p2p.broker_transactions import ReceiptTicket
from tests.test_ptz_prepare import ENTRY, IDENTITY

PATHS = ("ProConst._productInfo", "ProConst._versionInfo", "ProReadonly.devInfo")
REQUESTS = tuple(ReadRequest(ReceiptTicket(1, 17 + index, 0xB7), 123456, path)
                 for index, path in enumerate(PATHS))
VALUES = (dict(productID="6442451494", productModel="GW-IPC-AK-AV100.25", revision=1),
          dict(swVer="40.1.14", sdkVer="16.20.16355", hwVer=""),
          dict(t=1, stVal=dict(ptzInfo=dict(id0_status=7))))
COMPLETIONS = tuple(ReadCompletion(request, "response", None, 0, value)
                    for request, value in zip(REQUESTS, VALUES, strict=True))
PROFILE = PtzProfile(None, frozenset({"left", "right", "up", "down"}))


def derive(**changes):
    return grant_from_completions(ENTRY, **(dict(camera_id="cam_test", profile=PROFILE,
        generation=1, requests=REQUESTS, completions=COMPLETIONS,
        started_at=10.0, clock=lambda: 11.0) | changes))


def test_model_and_axes_grant_without_per_camera_shortcut():
    evidence = derive(completions=tuple(reversed(COMPLETIONS)))
    grant = evidence.for_generation(1, clock=lambda: 19.9)
    assert grant.device_id == 123456 and grant.access_id == 42
    assert grant.directions == PROFILE.directions
    assert "123456" not in repr(evidence) and "cam_test" not in repr(evidence)
    for generation, now in ((2, 11), (True, 11), (1, 20), (1, 9), (1, float("nan"))):
        with pytest.raises(ValueError): evidence.for_generation(generation, clock=lambda now=now: now)


def test_axes_and_reviewed_scope_both_restrict_grant():
    completions = (*COMPLETIONS[:2], replace(COMPLETIONS[2],
        value=dict(t=1, stVal=dict(ptzInfo=dict(id0_status=3)))),)
    assert derive(completions=completions).grant.directions == frozenset({"left", "right"})
    assert derive(profile=PtzProfile(None, frozenset({"right"}))).grant.directions == frozenset({"right"})


@pytest.mark.parametrize("value", [dict(t=1, stVal=dict(ptzInfo=dict(id0_status=0))),
    dict(t=0, stVal=dict(ptzInfo=dict(id0_status=7))),
    dict(t=1, stVal=dict(ptzInfo=dict(id1_status=7))), None])
def test_missing_or_wrong_axis_evidence_never_grants(value):
    with pytest.raises(ValueError):
        derive(completions=(*COMPLETIONS[:2], replace(COMPLETIONS[2], value=value)))


@pytest.mark.parametrize("fault", ["device", "generation", "sequence", "duplicate", "kind",
    "ack_only", "error", "bool_error", "transport", "bool_transport", "timeout"])
def test_only_exact_current_successful_camera_completions_grant(fault):
    first = COMPLETIONS[0]
    if fault == "device": first = replace(first, request=replace(first.request, device_id=654321))
    if fault == "generation": first = replace(first, request=replace(first.request, ticket=ReceiptTicket(2, 17, 0xB7)))
    if fault == "sequence": first = replace(first, request=replace(first.request, ticket=ReceiptTicket(1, 30, 0xB7)))
    if fault == "kind": first = replace(first, request=replace(first.request, ticket=ReceiptTicket(1, 17, 0xA0)))
    if fault == "duplicate": first = COMPLETIONS[1]
    if fault == "ack_only": first = replace(first, value=None)
    if fault == "error": first = replace(first, error_code=1)
    if fault == "bool_error": first = replace(first, error_code=False)
    if fault == "transport": first = replace(first, transport_result=1)
    if fault == "bool_transport": first = replace(first, transport_result=False)
    if fault == "timeout": first = replace(first, status="timeout")
    with pytest.raises(ValueError): derive(completions=(first, *COMPLETIONS[1:]))


@pytest.mark.parametrize("changes", [dict(camera_id="other"), dict(generation=2),
    dict(started_at=1), dict(started_at=12), dict(started_at=float("nan")),
    dict(requests=REQUESTS[:2]), dict(completions=COMPLETIONS[:2]),
    dict(requests=(REQUESTS[0], REQUESTS[0], REQUESTS[2])),
    dict(profile=PtzProfile(IDENTITY, PROFILE.directions))])
def test_wrong_binding_stale_partial_or_mismatched_identity_fails_closed(changes):
    with pytest.raises(ValueError): derive(**changes)


def test_unknown_firmware_is_not_promoted_from_candidate_directions():
    version = replace(COMPLETIONS[1], value=dict(VALUES[1], swVer="40.1.99"))
    with pytest.raises(ValueError): derive(completions=(COMPLETIONS[0], version, COMPLETIONS[2]))


def test_boolean_generation_cannot_match_integer_generation_by_dataclass_equality():
    first = COMPLETIONS[0]
    forged = replace(first, request=replace(first.request, ticket=ReceiptTicket(True, 17, 0xB7)))
    assert forged.request == first.request  # Python considers True == 1.
    with pytest.raises(ValueError): derive(completions=(forged, *COMPLETIONS[1:]))


@pytest.mark.parametrize("target", ["request", "completion"])
def test_malformed_ticket_fails_closed_without_attribute_error(target):
    bad = replace(REQUESTS[0], ticket=None)
    changes = (dict(requests=(bad, *REQUESTS[1:])) if target == "request" else
               dict(completions=(replace(COMPLETIONS[0], request=bad), *COMPLETIONS[1:])))
    with pytest.raises(ValueError): derive(**changes)
