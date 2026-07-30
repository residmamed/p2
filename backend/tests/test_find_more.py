"""Per-store "find more" paging (app/bestsellers.more_from_site) — no network.

The store buttons are only trustworthy if "no more" means the store is finished
rather than that the paging quietly broke. These tests pin the difference, plus
the two ways a deeper slice is actually obtained: actors are asked for a longer
list and the tail is returned, while SerpApi is asked for a later page.
"""
import pytest

from app import bestsellers
from app.models import Product


def make_products(site, n, start=1):
    return [
        Product(
            site=site,
            title=f"{site} product {i}",
            product_url=f"https://example.com/{site}/{i}",
            rating=4.5,
            review_count=100 + i,
        )
        for i in range(start, start + n)
    ]


@pytest.fixture(autouse=True)
def no_claude(monkeypatch):
    """The relevance filter is a separate concern with its own tests, and it
    needs a network call. Off here so these tests measure paging only."""
    monkeypatch.setattr(bestsellers.settings, "claude_relevance_filter", False)


# --- the apify path: ask for more, return the tail ------------------------

@pytest.mark.asyncio
async def test_actor_is_asked_for_the_rows_already_shown_plus_a_batch(monkeypatch):
    seen = {}

    async def fake_fetch(site_id, query, max_items=None):
        seen["site"], seen["query"], seen["max_items"] = site_id, query, max_items
        return make_products("target", max_items), []

    monkeypatch.setattr(bestsellers.apify_retail, "fetch_site", fake_fetch)

    response = await bestsellers.more_from_site("desk lamp", "target", have=40)

    # No actor here accepts an offset, so the whole list is refetched and sliced.
    assert seen["max_items"] == 40 + bestsellers.MORE_BATCH
    assert seen["site"] == "target"
    # Only the rows the caller doesn't already have come back.
    assert len(response.results) == bestsellers.MORE_BATCH
    assert response.results[0].title == "target product 41"


@pytest.mark.asyncio
async def test_a_store_with_nothing_further_returns_empty_not_an_error(monkeypatch):
    """The store has 40 rows in total and 40 are shown. Being finished is an
    ordinary outcome — the button says "no more"."""
    async def fake_fetch(site_id, query, max_items=None):
        return make_products("wayfair", 40), []

    monkeypatch.setattr(bestsellers.apify_retail, "fetch_site", fake_fetch)

    response = await bestsellers.more_from_site("desk lamp", "wayfair", have=40)
    assert response.results == []


@pytest.mark.asyncio
async def test_scores_are_assigned_over_the_whole_list_then_sliced(monkeypatch):
    """Normalized Score is a 0-1 scale defined within one store's result set. If
    the tail were scored on its own the scale would restart partway down the
    list, making row 41 look as strong as row 1."""
    async def fake_fetch(site_id, query, max_items=None):
        return make_products("target", max_items), []

    monkeypatch.setattr(bestsellers.apify_retail, "fetch_site", fake_fetch)

    response = await bestsellers.more_from_site("desk lamp", "target", have=20)

    # Site Rank continues the store's own numbering rather than restarting at 1.
    assert response.results[0].site_rank == 21
    # And nothing in the tail is scored as though it led the list.
    assert all(p.normalized_score is None or p.normalized_score < 1.0 for p in response.results)


@pytest.mark.asyncio
async def test_rows_already_shown_are_never_returned_twice(monkeypatch):
    async def fake_fetch(site_id, query, max_items=None):
        return make_products("etsy", max_items), []

    monkeypatch.setattr(bestsellers.apify_retail, "fetch_site", fake_fetch)

    first = await bestsellers.more_from_site("mug", "etsy", have=10)
    second = await bestsellers.more_from_site("mug", "etsy", have=10 + len(first.results))

    urls = {p.product_url for p in first.results}
    assert urls and not urls & {p.product_url for p in second.results}


# --- the SerpApi path: ask for a later page -------------------------------

@pytest.mark.asyncio
async def test_serpapi_sites_are_paged_rather_than_refetched(monkeypatch):
    seen = {}

    async def fake_search(site_id, query, page=1):
        seen["page"] = page
        return make_products("amazon", 16), []

    monkeypatch.setattr(bestsellers.serpapi_retail, "search", fake_search)

    response = await bestsellers.more_from_site("water bottle", "amazon", have=bestsellers.MORE_BATCH)

    assert seen["page"] == 2
    # A page request returns only that page, so none of it has been seen before
    # and the whole page is new — nothing is sliced off the front.
    assert len(response.results) == 16


@pytest.mark.asyncio
async def test_serpapi_failure_is_reported_not_raised(monkeypatch):
    async def fake_search(site_id, query, page=1):
        raise bestsellers.serpapi_retail.SerpApiError("quota exhausted")

    monkeypatch.setattr(bestsellers.serpapi_retail, "search", fake_search)

    response = await bestsellers.more_from_site("water bottle", "walmart", have=24)
    assert response.results == []
    assert "quota exhausted" in response.warnings[0]


# --- stores and depths that can't page ------------------------------------

@pytest.mark.asyncio
async def test_a_store_that_returns_everything_at_once_says_so():
    """IKEA comes through Zyte's productList, which has no page parameter, and
    returns its whole result set in one request. Saying that is better than
    re-requesting page 1 and calling the same rows new."""
    response = await bestsellers.more_from_site("lamp", "ikea", have=9)
    assert response.results == []
    assert "nothing further" in response.warnings[0]
    assert "IKEA" in response.warnings[0]


@pytest.mark.asyncio
async def test_paging_stops_at_a_depth_ceiling(monkeypatch):
    """Each press refetches everything before it, so cost grows while the number
    of new rows stays flat. The ceiling stops that, and explains itself."""
    called = False

    async def fake_fetch(*a, **kw):
        nonlocal called
        called = True
        return [], []

    monkeypatch.setattr(bestsellers.apify_retail, "fetch_site", fake_fetch)

    response = await bestsellers.more_from_site("mug", "target", have=bestsellers.MORE_MAX_DEPTH)
    assert response.results == []
    assert not called, "the ceiling must be checked before anything is billed"
    assert "Narrow the keyword" in response.warnings[0]


# --- the actor input that makes deeper slices possible -------------------

def test_page_capped_actors_scale_their_page_count_with_the_request():
    """Target and eBay cap by page as well as by count, so asking for 64 rows
    with maxSearchPages=1 would silently return 24."""
    from app.apify_retail import ACTORS, DEFAULT_MAX_ITEMS, MAX_SEARCH_PAGES, _pages_for

    # An ordinary search stays on one page however many rows it asks for: the
    # second page would double the cost of every search on the app's busiest
    # path to fill in rows below the fold.
    assert _pages_for(DEFAULT_MAX_ITEMS) == 1
    # Past that, only "find more" is asking, and it pays for the pages it needs.
    assert _pages_for(DEFAULT_MAX_ITEMS + 1) == 2
    assert _pages_for(64) == 3
    assert _pages_for(10_000) == MAX_SEARCH_PAGES  # never unbounded

    assert ACTORS["target"].build_input("q", DEFAULT_MAX_ITEMS)["maxSearchPages"] == 1
    assert ACTORS["target"].build_input("q", 64)["maxSearchPages"] == 3
    assert ACTORS["ebay"].build_input("q", 64)["maxSearchPages"] == 3
