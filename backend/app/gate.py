"""Access gate for the hosted deployment.

Every endpoint in this app spends real money — a single search fans out to
Zyte, SerpApi, Oxylabs and Anthropic — and none of them are free. Locally that
is fine: the only caller is the developer. Published at a URL, the same code is
an open faucet on the owner's API accounts, and the first person to find it
decides how much of their budget gets spent.

So the hosted deployment gets two controls, both off unless configured, so that
running locally is byte-for-byte the behaviour it has always had:

  APP_PASSWORD      a shared password. Unset => no gate at all (local dev).
  RATE_LIMIT_PER_HOUR   per-IP ceiling on the endpoints that cost money.

The password is deliberately *not* a user system. There are no accounts, no
per-user quotas and no billing here — this is the "people I gave the password
to" tier, and it should not be mistaken for the multi-tenant one. What it buys
is that a crawler, a scraper, or a stranger with the URL cannot spend anything.

The cookie carries an expiry and an HMAC over it, keyed by the password itself.
That means changing APP_PASSWORD invalidates every outstanding session for
free, which is the property you actually want when a password leaks.
"""

import hashlib
import hmac
import logging
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)

COOKIE_NAME = "p2_session"
SESSION_TTL = 30 * 24 * 3600  # 30 days; this is a convenience gate, not a bank

# Reachable without a session. /api/health is here so the platform's own health
# check does not need the password — Fly marks the machine unhealthy and
# restarts it forever otherwise.
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/status"}

# Rate limiting applies only to what costs money. Cheap local reads (a cached
# crop, a health check) are exempt, because counting them against the ceiling
# would lock a legitimate user out mid-session for doing nothing expensive.
FREE_PREFIXES = (
    "/api/health",
    "/api/auth/status",
    "/api/auth/logout",  # signing out must never be what the ceiling refuses
    "/api/trending/crop/",
)

# Login is exempt from the *spending* ceiling but not from all limits: it is the
# one endpoint reachable without a session, which makes it the one an attacker
# can hammer to guess the password. A separate, much lower ceiling caps that
# without affecting anything a real user does — nobody signs in ten times an hour.
LOGIN_PATH = "/api/auth/login"
LOGIN_ATTEMPTS_PER_HOUR = 10


def _password() -> str:
    return os.environ.get("APP_PASSWORD", "")


def cookie_domain() -> str | None:
    """The domain the session cookie is scoped to.

    Set COOKIE_DOMAIN=.paraphoria.com and the cookie covers both the page
    (paraphoria.com) and the API (api.paraphoria.com). That is what keeps it a
    *first-party* cookie despite the two being different origins — browsers
    judge that by registrable domain, not by origin, so the session survives
    Safari's tracking protection and the phase-out of third-party cookies.

    Unset => host-only, which is what local development wants.
    """
    return os.environ.get("COOKIE_DOMAIN") or None


def enabled() -> bool:
    """The gate exists only where it was configured. Unset => local dev."""
    return bool(_password())


def _sign(expires_at: int) -> str:
    key = _password().encode()
    mac = hmac.new(key, str(expires_at).encode(), hashlib.sha256).hexdigest()
    return f"{expires_at}.{mac}"


def issue_cookie() -> str:
    return _sign(int(time.time()) + SESSION_TTL)


def _valid_cookie(raw: str | None) -> bool:
    if not raw or "." not in raw:
        return False
    expires_str, _, mac = raw.partition(".")
    try:
        expires_at = int(expires_str)
    except ValueError:
        return False
    if expires_at < time.time():
        return False
    # compare_digest over the whole token, so a wrong password and a tampered
    # expiry fail identically and in constant time.
    return hmac.compare_digest(_sign(expires_at), raw)


def check_password(candidate: str) -> bool:
    return bool(candidate) and hmac.compare_digest(candidate, _password())


# --- rate limiting -----------------------------------------------------------
#
# A plain sliding window per IP, held in memory. In memory is the right call
# here and not a shortcut: this runs as a single Fly machine, and a Redis
# dependency to protect a handful of users would cost more to operate than the
# API spend it guards. If this ever scales past one instance, the counter has
# to move out of process — until then, shared state would be a fiction anyway.

_hits: dict[str, deque[float]] = defaultdict(deque)
_login_hits: dict[str, deque[float]] = defaultdict(deque)


def _over_window(bucket: dict[str, deque[float]], key: str, ceiling: int) -> bool:
    """Sliding one-hour window. Records the hit unless it would exceed."""
    now = time.time()
    window = bucket[key]
    while window and now - window[0] > 3600:
        window.popleft()
    if len(window) >= ceiling:
        return True
    window.append(now)
    return False


def _limit() -> int:
    try:
        return int(os.environ.get("RATE_LIMIT_PER_HOUR", "0"))
    except ValueError:
        return 0


def client_ip(request: Request) -> str:
    """The real caller's IP, as seen from behind the Netlify proxy.

    Every request arrives from Netlify's egress, so request.client.host is the
    proxy for all callers and useless to count against. Netlify appends the
    original client to X-Forwarded-For, and its first entry is that client.

    This header is caller-supplied and therefore spoofable. That is acceptable
    *because it is the second line, not the first*: the password gate is what
    keeps strangers out, and the rate limit only shapes the behaviour of people
    already holding the password.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(request: Request) -> bool:
    path = request.url.path

    # Brute-force protection. Applied whenever the gate is on, independently of
    # RATE_LIMIT_PER_HOUR: this one guards the password itself, and it would be
    # the wrong thing to leave switched off by default. Ten an hour is invisible
    # to a real user — nobody signs in repeatedly — and useless for guessing.
    if path == LOGIN_PATH:
        return _over_window(_login_hits, client_ip(request), LOGIN_ATTEMPTS_PER_HOUR)

    ceiling = _limit()
    if ceiling <= 0 or path.startswith(FREE_PREFIXES):
        return False
    return _over_window(_hits, client_ip(request), ceiling)


async def middleware(request: Request, call_next):
    """Gate every API route. Non-API paths are left alone — the frontend is
    served by Netlify, not from here, so anything else is a stray request."""
    path = request.url.path

    if not enabled() or not path.startswith("/api/"):
        return await call_next(request)

    if path not in PUBLIC_PATHS:
        if not _valid_cookie(request.cookies.get(COOKIE_NAME)):
            return JSONResponse({"detail": "Not signed in"}, status_code=401)

    if _rate_limited(request):
        log.warning("rate limit hit for %s on %s", client_ip(request), path)
        detail = (
            f"Too many sign-in attempts ({LOGIN_ATTEMPTS_PER_HOUR}/hour). Try again later."
            if path == LOGIN_PATH
            else (
                f"Rate limit reached ({_limit()} requests/hour). This protects the "
                "API budget this app runs on. Try again later."
            )
        )
        return JSONResponse({"detail": detail}, status_code=429)

    return await call_next(request)


def require_enabled() -> None:
    """Login is meaningless where no password is configured; say so plainly
    rather than accepting any string and minting a session."""
    if not enabled():
        raise HTTPException(400, "No password is configured on this deployment.")
