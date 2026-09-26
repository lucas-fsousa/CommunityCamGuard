"""Bounded login pacing with synthetic clocks/clients; no production traffic."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app import login_throttle
from backend.app.api import auth as routes
from backend.app.login_throttle import LoginThrottle


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    # Patch this module's clock, not the global time module used by TestClient.
    class Clock:
        @staticmethod
        def monotonic():
            return now[0]
    monkeypatch.setattr(login_throttle, "time", Clock)
    return now


def test_burst_refill_and_rejections_do_not_extend_lockout(clock):
    limiter = LoginThrottle()
    assert [limiter.retry_after("192.0.2.1") for _ in range(10)] == [0] * 10
    assert limiter.retry_after("192.0.2.1") == 6
    clock[0] += 5.1
    for _ in range(100):
        assert limiter.retry_after("192.0.2.1") == 1
    clock[0] += 0.9
    assert limiter.retry_after("192.0.2.1") == 0
    assert limiter.retry_after("192.0.2.1") == 6
    clock[0] += 60
    assert [limiter.retry_after("192.0.2.1") for _ in range(10)] == [0] * 10


def test_concurrent_attempts_reserve_exactly_one_burst(clock):
    limiter = LoginThrottle()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: limiter.retry_after("192.0.2.1"), range(40)))
    assert results.count(0) == 10
    assert results.count(6) == 30


def test_identity_normalization_and_fixed_memory(clock):
    limiter = LoginThrottle()
    assert limiter._slot("::ffff:192.0.2.1") == limiter._slot("192.0.2.1")
    assert limiter._slot("2001:db8::1") == limiter._slot("2001:db8::ffff")
    assert limiter._slot("unknown") == limiter._slot("another-invalid-host")
    table = limiter._next
    for number in range(10000):
        limiter.retry_after(f"198.18.{number // 256}.{number % 256}")
    assert limiter._next is table and len(table) == 4096


def test_other_bucket_unaffected_and_collisions_share_quota(clock, monkeypatch):
    limiter = LoginThrottle()
    monkeypatch.setattr(limiter, "_slot", lambda host: 1 if host == "other" else 0)
    for _ in range(10):
        assert limiter.retry_after("attacker") == 0
    assert limiter.retry_after("collision") == 6
    assert limiter.retry_after("other") == 0


def make_app():
    app = FastAPI()
    app.include_router(routes.router)
    return app


def test_route_throttles_before_validation_or_key_check_and_ignores_headers(clock, monkeypatch):
    checks = []
    monkeypatch.setattr(routes, "check_key", lambda key: checks.append(key) or False)
    with TestClient(make_app()) as client:
        for number in range(10):
            response = client.post("/api/login", json={"key": "wrong"}, headers={
                "X-Forwarded-For": f"192.0.2.{number}", "Forwarded": f"for=192.0.2.{number}",
                "X-Real-IP": f"192.0.2.{number}",
            })
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
        response = client.post("/api/login", content="not JSON")
        assert response.status_code == 429 and len(checks) == 10
        assert response.headers["retry-after"] == "6"
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        assert client.get("/api/me").status_code == 200
        assert client.post("/api/logout").status_code == 200
        clock[0] += 6
        assert client.post("/api/login", json={"key": "wrong"}).status_code == 401


def test_malformed_bodies_consume_quota(clock):
    with TestClient(make_app()) as client:
        for _ in range(10):
            assert client.post("/api/login", content="{").status_code == 422
        assert client.post("/api/login", json={"key": "test-secret-key"}).status_code == 429


def test_success_cannot_reset_budget_and_app_instances_are_isolated(clock):
    with TestClient(make_app()) as client:
        for _ in range(10):
            response = client.post("/api/login", json={"key": "test-secret-key"})
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
        assert client.post("/api/login", json={"key": "test-secret-key"}).status_code == 429
        assert client.get("/api/me").json()["authenticated"]
    with TestClient(make_app()) as other:
        assert other.post("/api/login", json={"key": "test-secret-key"}).status_code == 200


def test_bundled_launcher_disables_proxy_identity_rewriting():
    source = (Path(__file__).parents[1] / "backend/app/main.py").read_text()
    assert "uvicorn.run(app, host=s.host, port=s.port, proxy_headers=False)" in source


def test_login_throttle_feedback_is_localized():
    root = Path(__file__).parents[1] / "frontend"
    assert 'error.status === 429 ? "login.throttled"' in (root / "app.js").read_text()
    assert (root / "i18n.js").read_text().count('"login.throttled":') == 2
    assert '[408, 503].includes(error.status) ? "login.retry"' in (root / "app.js").read_text()
    assert (root / "i18n.js").read_text().count('"login.retry":') == 2
