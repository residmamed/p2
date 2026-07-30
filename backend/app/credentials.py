"""Per-vendor credential pools, so a vendor's ceiling can be raised by adding a
second account instead of waiting on the first one.

Every vendor behind this app meters something — SerpApi a monthly search quota,
Apify concurrent actor runs, Oxylabs a request rate, Zyte a rate limit. One key
means one ceiling, and the pipeline fans out hard enough to reach it: a supplier
prefetch over a full grid is one Lens search per product and, for the ones Lens
cannot answer, an Apify actor run per product per site.

A pool is a list of interchangeable credentials for one vendor, handed out round
robin. Nothing here retries or fails over — a pool spreads load across accounts,
which is what raises the ceiling; it does not make a rejected request succeed.

Configuration is by numbered environment variables: the existing name is the
first credential and `_2`, `_3`, … are additional ones.

    SERPAPI_KEY=abc          # one account, exactly as before
    SERPAPI_KEY_2=def        # a second, used for every other search
    SERPAPI_KEY_3=ghi

Numbered rather than a delimited list because two of these values are passwords,
and any delimiter is a value some password legitimately contains. Gaps are fine
(`_2` may be absent while `_3` is set); blanks are skipped. Setting none of the
numbered names leaves behaviour identical to a single key, which is what every
existing deployment has.

WHAT ROTATION DOES NOT SURVIVE: a multi-step flow against one vendor has to use
one credential throughout. An Apify run started on account A cannot be polled or
have its dataset read on account B — the run does not exist there. Callers with a
flow like that take a credential once and thread it through, rather than calling
`next()` at each step (see `apify_suppliers.search`).
"""
import itertools
import os
import threading
from pathlib import Path

from dotenv import dotenv_values

# `.env` is read relative to the working directory uvicorn is started from
# (backend/), matching how config.Settings resolves its own env_file.
ENV_FILE = Path(".env")

# How far the numbered scan goes. Well past any real account count — the loop is
# cheap and runs once at import — but bounded, so a typo like `SERPAPI_KEY_X`
# fails as "unused variable" rather than as an unbounded scan.
MAX_ACCOUNTS = 16


def _environment() -> dict[str, str]:
    """The .env file overlaid with the real environment, in that order.

    Same precedence as pydantic-settings: a value exported in the shell beats
    the one in the file, so a deployment can override without editing it.
    """
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        values.update({k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None})
    values.update(os.environ)
    return values


def _numbered(env: dict[str, str], base: str) -> list[str]:
    """`BASE`, `BASE_2`, … `BASE_MAX`, in order, skipping the ones not set."""
    names = [base] + [f"{base}_{i}" for i in range(2, MAX_ACCOUNTS + 1)]
    return [value for name in names if (value := env.get(name, "").strip())]


class KeyPool:
    """Interchangeable credentials for one vendor, handed out round robin.

    A credential is whatever that vendor needs to identify an account: a string
    for the token-based vendors, a (username, password) pair for Oxylabs. The
    pool does not care which — it hands back what it was given.
    """

    def __init__(self, label: str, values: list):
        self.label = label
        self._values = list(values)
        self._cycle = itertools.cycle(self._values) if self._values else None
        # Sourcing fans out with asyncio, but the Zyte scrapers reach for a key
        # from worker threads. A lock costs nothing at this call rate and makes
        # the pool correct under both.
        self._lock = threading.Lock()

    def __bool__(self) -> bool:
        return bool(self._values)

    def __len__(self) -> int:
        return len(self._values)

    @property
    def all(self) -> list:
        """Every credential, for callers that want to fan out across accounts
        deliberately rather than take the next one."""
        return list(self._values)

    def next(self):
        """The next credential, or None if the vendor is not configured.

        None rather than an empty string even for the string pools: a caller
        that forgets to check gets a type error at the call site instead of a
        401 from the vendor twenty seconds later.
        """
        if not self._values:
            return None
        if len(self._values) == 1:
            return self._values[0]
        with self._lock:
            return next(self._cycle)


def _build() -> dict[str, KeyPool]:
    env = _environment()
    # Oxylabs identifies an account by a pair, so the two lists are zipped
    # positionally: OXYLABS_USERNAME_2 pairs with OXYLABS_PASSWORD_2. zip stops
    # at the shorter one, so a username with no matching password is dropped
    # rather than sent as a half credential.
    oxylabs = list(
        zip(_numbered(env, "OXYLABS_USERNAME"), _numbered(env, "OXYLABS_PASSWORD"))
    )
    return {
        "zyte": KeyPool("ZYTE_API_KEY", _numbered(env, "ZYTE_API_KEY")),
        "apify": KeyPool("APIFY_TOKEN", _numbered(env, "APIFY_TOKEN")),
        "serpapi": KeyPool("SERPAPI_KEY", _numbered(env, "SERPAPI_KEY")),
        "oxylabs": KeyPool("OXYLABS_USERNAME/PASSWORD", oxylabs),
    }


_pools = _build()

ZYTE = _pools["zyte"]
APIFY = _pools["apify"]
SERPAPI = _pools["serpapi"]
# Yields (username, password).
OXYLABS = _pools["oxylabs"]


def summary() -> dict[str, int]:
    """How many accounts each vendor has, for the startup log and /api/health.

    Counts only — never the credentials themselves, which is why this returns a
    dict of ints rather than anything that could be logged whole by accident.
    """
    return {name: len(pool) for name, pool in _pools.items()}
