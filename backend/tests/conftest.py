"""Suite-wide guards.

The one below exists because a default was changed and the test suite quietly
started spending money. `supplier_enrichment_backend` defaults to "browserbase",
which opens a real cloud browser per product page — so every test that reached
the enrichment step began making live Browserbase calls. The suite went from
1.7s to 144s, ten tests failed on network behaviour rather than on logic, and
each run billed real sessions.

That is the failure mode this file prevents: a unit test must never be one
config default away from talking to a paid vendor. The fixture pins the backend
to the offline-testable one for every test in the suite, so a test that wants
the browser path has to ask for it explicitly and visibly.
"""
import pytest

from app import lens_suppliers
from app.config import settings


@pytest.fixture(autouse=True)
def no_live_cloud_browsers(monkeypatch):
    """Pin supplier enrichment to the Oxylabs path for every test.

    Not "disable enrichment" — that would make the enrichment tests vacuous.
    The Oxylabs path is the one whose transport the tests already stub, so
    pinning it keeps them testing the parsing and merging logic they are about,
    with no network either way.

    A test that genuinely wants the Browserbase branch overrides this with its
    own monkeypatch, which then reads as a deliberate choice at the call site
    rather than as an accident of configuration.
    """
    monkeypatch.setattr(settings, "supplier_enrichment_backend", "oxylabs")


@pytest.fixture(autouse=True)
def fixed_enrichment_cap(monkeypatch):
    """Pin how many pages one search may open.

    The cap is configurable (`supplier_enrich_max_pages`) and was raised from 10
    to 25 when the Browserbase backend arrived, which broke three tests that
    build a fixed number of candidates and assert the cap bites. Those tests are
    about the capping mechanism, not about today's number, so the number is held
    still here rather than restated in each of them.
    """
    monkeypatch.setattr(lens_suppliers, "MAX_ENRICH", 10)


@pytest.fixture(autouse=True)
def no_page_cache(monkeypatch):
    """Disable the enriched-page cache for every test.

    It ships enabled with a 6-hour TTL, which in a test suite means one test's
    parse is served to the next: results stop depending only on the fixture the
    test declares, and a suite that passes clean fails on a second run. It also
    writes real files into backend/.cache/pages as a side effect of unit tests.

    Tests that are about the cache set their own TTL.
    """
    monkeypatch.setattr(settings, "supplier_page_cache_ttl_minutes", 0)
