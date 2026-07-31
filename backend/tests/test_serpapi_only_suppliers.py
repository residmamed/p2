"""SerpApi-only supplier search: what it returns, and what it must not call.

Two separate promises are pinned here, because breaking either is silent.

The first is that no second vendor is reached. The mode exists to make SerpApi
the only thing a supplier search calls, and a stray Oxylabs fetch would still
produce a perfectly good-looking response — just a bill and a latency spike
nobody asked for. The tripwire below fails on *any* attribute access, so the
test does not depend on guessing which method would have been used.

The second is that rows still come back, flagged. The pipeline's rule is that it
must never return an empty list without saying why, and "we chose not to open
the page" is a reason the caller has to be told: without the flag, a row with no
supplier name is indistinguishable from a supplier who publishes no name.
"""
import pytest

from app import lens_suppliers as ls

CANDIDATES = [
    ls.LensCandidate(
        title="40oz Double Wall Vacuum Insulated Tumbler",
        product_url="https://wisdomhouseware.en.made-in-china.com/product/yESUfcRVZehC/x.html",
        source_domain="wisdomhouseware.en.made-in-china.com",
        marketplace="made_in_china",
        image_url="https://serpapi.com/searches/abc/images/x.jpeg",
        price_text="$3.20",
        source_name="Made-in-China",
        match_confidence="lens_visual_match",
        order=0,
        resolvable=True,
    ),
    ls.LensCandidate(
        title="Wholesale Stainless Steel Tumbler",
        product_url="https://www.alibaba.com/product-detail/x_1601234567890.html",
        source_domain="www.alibaba.com",
        marketplace="alibaba",
        image_url="https://serpapi.com/searches/abc/images/y.jpeg",
        price_text=None,
        source_name="Alibaba.com",
        match_confidence="lens_exact_match",
        order=1,
        resolvable=True,
    ),
]


class OxylabsTripwire:
    """Any use at all is the failure. Attribute-level rather than method-level so
    the test keeps working if the enrichment step's call site changes shape."""

    def __getattr__(self, name):
        raise AssertionError(f"Oxylabs was reached in SerpApi-only mode (.{name})")


@pytest.fixture
def serpapi_only(monkeypatch):
    monkeypatch.setattr(ls.settings, "supplier_search_serpapi_only", True)


@pytest.mark.asyncio
async def test_no_second_vendor_is_called(serpapi_only):
    rows, _, _ = await ls._enrich(CANDIDATES, OxylabsTripwire())
    assert len(rows) == len(CANDIDATES)


@pytest.mark.asyncio
async def test_every_candidate_survives_rather_than_being_dropped(serpapi_only):
    """A thin record is still a lead. Dropping the rows that could not be
    enriched would turn "we didn't look" into "there is nothing here"."""
    rows, _, _ = await ls._enrich(CANDIDATES, OxylabsTripwire())
    assert [r.product_url for r in rows] == [c.product_url for c in CANDIDATES]


@pytest.mark.asyncio
async def test_rows_are_flagged_unenriched_with_a_reason(serpapi_only):
    """Without this, a row with no supplier name reads as a supplier who
    publishes no name, which is a different and wrong claim."""
    rows, _, _ = await ls._enrich(CANDIDATES, OxylabsTripwire())
    assert all(r.enriched is False for r in rows)
    assert all("SerpApi-only" in (r.enrichment_error or "") for r in rows)


@pytest.mark.asyncio
async def test_the_fields_only_the_product_page_carries_are_absent(serpapi_only):
    """The cost of the mode, pinned so it cannot be quietly misreported later.
    Lens carries no supplier name and no MOQ, so neither can appear."""
    rows, _, _ = await ls._enrich(CANDIDATES, OxylabsTripwire())
    assert all(r.supplier_name is None for r in rows)
    assert all(r.moq is None for r in rows)


@pytest.mark.asyncio
async def test_the_warning_says_which_setting_caused_it(serpapi_only):
    """A user looking at a grid with no supplier names needs to be able to find
    out that it was a configuration choice, not a failed lookup."""
    _, warnings, errors = await ls._enrich(CANDIDATES, OxylabsTripwire())
    assert errors == []
    assert len(warnings) == 1
    assert "SUPPLIER_SEARCH_SERPAPI_ONLY" in warnings[0]


@pytest.mark.asyncio
async def test_no_candidates_short_circuits_before_the_mode_is_consulted(serpapi_only):
    assert await ls._enrich([], OxylabsTripwire()) == ([], [], [])


@pytest.mark.asyncio
async def test_mode_off_still_reaches_the_enrichment_step(monkeypatch):
    """The switch has to be a switch. If turning it off left the Oxylabs path
    unreachable, the mode would be a one-way door and this suite would still
    pass on every other test."""
    monkeypatch.setattr(ls.settings, "supplier_search_serpapi_only", False)
    with pytest.raises(AssertionError, match="Oxylabs was reached"):
        await ls._enrich(CANDIDATES, OxylabsTripwire())


# --- ratings, and the layer that used to swallow them -----------------------
#
# These live here rather than in the parser's own file because the bug they pin
# was not in the extraction at all. `_blob_number` read Alibaba's `averageStar`
# correctly from the first live page it was pointed at; `_fill` then dropped it,
# because that function copies a hardcoded list of field names and nobody had
# added the new ones. The parser returned rating=None on a page serving
# `"averageStar":4.6`, which reads exactly like a site that publishes no rating.

from app.parsing.marketplace_product import ParsedProduct, _fill, parse_product_page

ALIBABA_RATED = """
<html><body><script>window.__data = {
  "subject":"33pcs Silicone Kitchen Utensil Set",
  "companyName":"Yongkang Gcook Imp&Exp Co., Ltd.",
  "averageStar":4.6, "totalReviewCount":310,
  "minOrderQuantity":10, "productUnit":"pieces"
}</script></body></html>
"""


def test_alibaba_rating_and_review_count_are_read():
    """Shape taken from a live page: averageStar 4.6, totalReviewCount 310."""
    p = parse_product_page(ALIBABA_RATED, "alibaba")
    assert (p.rating, p.review_count) == (4.6, 310)


def test_fill_carries_the_rating_between_layers():
    """The actual bug. Extraction worked; this copy step silently dropped it, so
    a rated page looked like an unrated site."""
    target, source = ParsedProduct(), ParsedProduct(rating=4.6, review_count=310)
    _fill(target, source)
    assert (target.rating, target.review_count) == (4.6, 310)


def test_fill_moves_rating_and_count_together():
    """A count belongs to the stars it counted. Taking one layer's rating and
    another's count would report reviews that never backed that figure."""
    target = ParsedProduct(rating=4.0, review_count=5)
    _fill(target, ParsedProduct(rating=2.0, review_count=9999))
    assert (target.rating, target.review_count) == (4.0, 5)


def test_a_rating_outside_zero_to_five_is_not_a_rating():
    """These keys sit in a blob next to prices and counts. A loose match that
    caught the wrong one would put 99 or 7.20 in the stars column."""
    for bad in ('"averageStar":99', '"averageStar":0', '"averageStar":7.2'):
        html = f'<html><body><script>{{{bad},"subject":"x","companyName":"y"}}</script></body></html>'
        assert parse_product_page(html, "alibaba").rating is None


def test_a_review_count_without_a_rating_is_dropped():
    """A count with no stars behind it is not evidence of anything."""
    html = '<html><body><script>{"totalReviewCount":310,"subject":"x","companyName":"y"}</script></body></html>'
    p = parse_product_page(html, "alibaba")
    assert p.rating is None and p.review_count is None


def test_made_in_china_gets_no_rating_from_alibaba_keys():
    """Measured: MIC publishes no rating. The Alibaba keys must not be applied
    to it — a stray match would invent a figure the site never stated."""
    assert parse_product_page(ALIBABA_RATED, "made_in_china").rating is None


def test_a_page_with_only_a_rating_is_still_empty():
    """Otherwise a bare star figure counts as a successful parse and lands on a
    row with no title, price or supplier."""
    html = '<html><body><script>{"averageStar":4.6,"totalReviewCount":310}</script></body></html>'
    assert parse_product_page(html, "alibaba").is_empty()


# --- directory pages are not listings ---------------------------------------

def _cand(url, marketplace):
    return ls.LensCandidate(
        title="x", product_url=url, source_domain="d", marketplace=marketplace,
        image_url=None, price_text=None, source_name=None,
        match_confidence="lens_visual_match", order=0, resolvable=True,
    )


def test_an_alibaba_category_page_is_not_a_product_page():
    """Measured live: /countrysearch/ enriched into a real supplier name, a
    "50 sets" MOQ and a price of $1,000,000. Every field looked plausible and
    the row was about a directory, not a factory."""
    url = "https://www.alibaba.com/countrysearch/CN/cooking-utensils-and-gadgets.html"
    assert ls._is_product_page(_cand(url, "alibaba")) is False


def test_an_ordinary_alibaba_listing_still_passes():
    url = "https://www.alibaba.com/product-detail/Bpa-free-Kitchen-Gadgets_1600881075351.html"
    assert ls._is_product_page(_cand(url, "alibaba")) is True


def test_an_unfamiliar_alibaba_listing_shape_is_not_rejected():
    """The Alibaba test is a deny-list on purpose: dropping a real supplier
    because its URL shape is new is worse than admitting a rare category page."""
    assert ls._is_product_page(_cand("https://www.alibaba.com/p-detail/x_999.html", "alibaba")) is True


def test_made_in_china_is_still_tested_the_other_way_round():
    """MIC must look like a listing to pass — its directory pages are common."""
    assert ls._is_product_page(_cand("https://m.made-in-china.com/hot-search/x.html", "made_in_china")) is False
    assert ls._is_product_page(_cand("https://x.en.made-in-china.com/product/abc/y.html", "made_in_china")) is True


def test_an_alibaba_keyword_category_page_is_not_a_product_page():
    """Language subdomains serve category pages under /g/. This one survived the
    first deny-list and showed up as the only page still opening a browser on an
    otherwise fully cached run — a category page never parses, so it never
    caches, so it is paid for on every single search."""
    url = "https://french.alibaba.com/g/silicone-kitchenaid-utensils.html"
    assert ls._is_product_page(_cand(url, "alibaba")) is False


def test_an_alibaba_brand_landing_page_is_not_a_product_page():
    """`alibaba.com/premium/nuna.html` — Alibaba serves a login wall here, so
    Google indexed the wall and Lens returned it titled "Sign In - Alibaba.com".
    Two of them reached the listing column of a real search."""
    assert ls._is_product_page(_cand("https://www.alibaba.com/premium/nuna.html", "alibaba")) is False


def test_a_login_wall_title_is_rejected_whatever_the_url():
    """The path deny-list cannot carry this alone: a login wall can be served
    from any URL, and on an ordinary-looking one the title is the only tell."""
    assert ls._is_listing_title("Sign In - Alibaba.com") is False
    assert ls._is_listing_title("sign in") is False
    assert ls._is_listing_title("Security Check") is False


def test_a_real_listing_containing_those_words_still_passes():
    """Whole-title match, not substring: a genuine product called "Sign In Sheet
    Printing Service" contains the words but is not a login page."""
    assert ls._is_listing_title("Custom Sign In Sheet Printing Service A4") is True
    assert ls._is_listing_title("44-piece Silicone Kitchen Tool Set") is True
