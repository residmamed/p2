"""The access gate: who gets to spend the API budget.

Worth testing carefully because both failure directions are quiet. A gate that
fails open publishes an app where every request costs real money to whoever
finds the URL, and nothing in the UI would look wrong. A gate that fails closed
locks the owner out of their own tool, or — worse — starts answering the
platform's health check with 401 and the machine restart-loops forever.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app import gate
from app.main import app

PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", PASSWORD)
    # Each test starts from an empty window; the counters are module-level and
    # would otherwise leak across tests in whatever order they happen to run.
    gate._hits.clear()
    gate._login_hits.clear()
    # https, because the session cookie is Secure and a client will refuse to
    # store it over plain http — the same rule a browser applies.
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def open_client(monkeypatch):
    """No password configured — every local run, and the pre-gate behaviour."""
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    gate._hits.clear()
    gate._login_hits.clear()
    # https, because the session cookie is Secure and a client will refuse to
    # store it over plain http — the same rule a browser applies.
    return TestClient(app, base_url="https://testserver")


def test_no_password_configured_leaves_every_route_open(open_client):
    """The gate is a property of the deployment, not of the build. Unset, it
    must not change a single thing about running locally."""
    assert gate.enabled() is False
    assert open_client.get("/api/auth/status").json()["signed_in"] is True


def test_protected_route_rejects_a_caller_with_no_session(client):
    # A path that no route claims. The gate is middleware, so it runs before
    # routing and rejects this exactly as it would a real endpoint — without
    # the test firing a live search at Zyte and SerpApi to prove it.
    response = client.get("/api/bestsellers-not-a-real-route")
    assert response.status_code == 401
    assert response.json()["detail"] == "Not signed in"


def test_health_stays_public(client):
    """Fly polls this to decide whether the machine is alive. Behind the gate it
    would read 401 as unhealthy and restart the machine in a loop."""
    assert client.get("/api/health").status_code == 200


def test_wrong_password_is_refused(client):
    response = client.post("/api/auth/login", json={"password": "guess"})
    assert response.status_code == 401
    assert gate.COOKIE_NAME not in response.cookies


def test_correct_password_opens_the_protected_routes(client):
    login = client.post("/api/auth/login", json={"password": PASSWORD})
    assert login.status_code == 200
    # TestClient keeps the cookie, exactly as a browser would.
    assert client.get("/api/auth/status").json()["signed_in"] is True


def test_session_cookie_is_not_readable_from_javascript(client):
    """HttpOnly is what stops an XSS anywhere in the app from lifting the
    session and spending the budget from somewhere else entirely."""
    login = client.post("/api/auth/login", json={"password": PASSWORD})
    header = login.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "secure" in header


def test_changing_the_password_invalidates_outstanding_sessions(client, monkeypatch):
    """The cookie is signed with the password itself, so rotating it revokes
    every live session. That is the property that makes a leak recoverable
    without a session store to purge."""
    client.post("/api/auth/login", json={"password": PASSWORD})
    assert client.get("/api/auth/status").json()["signed_in"] is True

    monkeypatch.setenv("APP_PASSWORD", "a different password")
    assert client.get("/api/auth/status").json()["signed_in"] is False


def test_expired_cookie_is_rejected(client, monkeypatch):
    monkeypatch.setattr(gate, "SESSION_TTL", -1)  # already in the past
    stale = gate.issue_cookie()
    assert gate._valid_cookie(stale) is False


def test_tampered_expiry_does_not_extend_a_session(client):
    """A forged far-future expiry must fail: the MAC covers the expiry, so
    editing it invalidates the signature rather than buying more time."""
    valid = gate.issue_cookie()
    _, _, mac = valid.partition(".")
    assert gate._valid_cookie(f"{int(time.time()) + 10**9}.{mac}") is False


def test_login_attempts_are_throttled_even_with_no_spend_limit(client, monkeypatch):
    """Login is exempt from the spending ceiling but not from all limits — it is
    the one route reachable without a session, so it is the one an attacker can
    hammer to guess the password."""
    monkeypatch.delenv("RATE_LIMIT_PER_HOUR", raising=False)
    codes = [
        client.post("/api/auth/login", json={"password": "guess"}).status_code
        for _ in range(gate.LOGIN_ATTEMPTS_PER_HOUR + 2)
    ]
    assert codes[0] == 401          # refused, but still being answered
    assert codes[-1] == 429         # eventually throttled
    assert 429 in codes


def test_spend_limit_counts_expensive_routes_and_ignores_free_ones(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_HOUR", "3")
    client.post("/api/auth/login", json={"password": PASSWORD})

    # Unrouted on purpose (see above): the ceiling is enforced in middleware, so
    # this counts identically to a real search without spending anything.
    codes = [client.get("/api/costs-money").status_code for _ in range(5)]
    assert codes[-1] == 429

    # Health is free, so a throttled caller can still be checked on.
    assert client.get("/api/health").status_code == 200


def test_client_ip_prefers_the_forwarded_caller(client):
    """Behind a CDN every request arrives from the proxy's own address, so
    counting request.client.host would put all users in one bucket."""
    request = type(
        "Req",
        (),
        {
            "headers": {"x-forwarded-for": "203.0.113.7, 198.51.100.1"},
            "client": type("C", (), {"host": "10.0.0.1"})(),
        },
    )()
    assert gate.client_ip(request) == "203.0.113.7"
