"""Scalar diagnostics reject credential-bearing or falsely truthy driver values."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import auth
from backend.app.api import provisioning_privileged as routes

SECRET = "SYNTHETIC_DIAGNOSTIC_SECRET"
CASES = [
    ("online-status", "online_status", dict(query_succeeded=True, online=False,
     terminal_failure=False, code=None, handoff_ready=False)),
    ("p2p-probe", "probe_inventory", dict(authenticated=True, device_count=2,
     online_count=1, target_visible=True, target_online=False, target_term_resolved=False,
     skipped_incomplete_nodes=0)),
    ("p2p-route-probe", "probe_route", dict(authenticated=True, target_visible=True,
     target_online=True, broker_acknowledged=True, route_advertised=True, direct_datagrams=2,
     direct_handshake=True, camera_contacted=True, broker_error_code=0)),
]


@pytest.mark.parametrize("route,method,fields", CASES)
@pytest.mark.parametrize("bad", [False, True])
def test_http_diagnostic_contract(monkeypatch, route, method, fields, bad):
    values = dict(fields)
    if bad:
        values[next(iter(values))] = SECRET
    values["private_token"] = SECRET
    provider = SimpleNamespace(**{method: lambda *args, **kwargs: SimpleNamespace(**values)})
    monkeypatch.setattr(routes, "onboarding", lambda: provider)
    monkeypatch.setattr(routes, "inspect_provisioning_label", lambda body: {"device_id": "12345678"})
    app = FastAPI(); app.include_router(routes.router)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 9000)) as client:
        client.cookies.set(auth.COOKIE_NAME, auth.issue_token())
        response = client.post(f"/api/provisioning/privileged/{route}", json={"attempt_id": SECRET})
    assert response.status_code == (502 if bad else 200)
    assert SECRET not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("field,bad", [("device_count", True), ("online_count", -1),
    ("device_count", 2**40), ("target_online", "true"), ("skipped_incomplete_nodes", {"token": SECRET})])
def test_invalid_counters_fail_closed(field, bad):
    from fastapi import HTTPException

    from backend.app.api.enrollment_results import diagnostic_result
    values = {**CASES[1][2], "camera_contacted": False, field: bad}
    with pytest.raises(HTTPException) as caught:
        diagnostic_result("inventory", values, device_id="12345678")
    assert caught.value.status_code == 502
    assert SECRET not in caught.value.detail
