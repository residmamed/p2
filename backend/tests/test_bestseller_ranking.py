"""Ranking tests — the rules that stop a confident-looking wrong answer.

No live calls. These pin the behaviours the live probes forced: sold counts
substitute for a missing best-seller sort, a site that offers neither says so
instead of faking a rank, and a weak signal never outranks a strong one just
because both normalize to 1.0.
"""
from app.bestsellers import (
    RANK_BASIS_WEIGHT,
    SITES,
    _assign_normalized_scores,
    _merge_and_rank,
)
from app.models import Product
from app.retail_browser import BROWSER_SITES, _parse_sold, harvest_to_products


def _p(site: str, title: str, **kw) -> Product:
    return Product(site=site, title=title, product_url=f"https://x/{title}", **kw)


# --- rank basis -----------------------------------------------------------

def test_bestseller_sort_ranks_by_list_position():
    products = [_p("amazon", f"a{i}") for i in range(5)]
    _assign_normalized_scores(products, SITES["amazon"])

    assert [p.rank_basis for p in products] == ["bestseller_sort"] * 5
    assert products[0].normalized_score == 1.0
    assert products[-1].normalized_score == 0.0


def test_sold_counts_outrank_page_position_when_published():
    """Temu publishes "N sold" — the listing with the most sales must win even
    though it sits lower on the page."""
    products = [_p("temu", "low", popularity_score=10), _p("temu", "high", popularity_score=9000)]
    _assign_normalized_scores(products, SITES["temu"])

    assert [p.rank_basis for p in products] == ["sold_count", "sold_count"]
    assert products[1].normalized_score > products[0].normalized_score


def test_site_claiming_sold_counts_degrades_visibly_when_none_are_published():
    products = [_p("temu", "a"), _p("temu", "b")]
    warnings = _assign_normalized_scores(products, SITES["temu"])

    assert all(p.rank_basis == "relevance" for p in products)
    assert any("sold counts" in w.lower() for w in warnings)


def test_relevance_only_site_warns_that_it_is_not_a_bestseller_ranking():
    products = [_p("ikea", "a"), _p("ikea", "b")]
    warnings = _assign_normalized_scores(products, SITES["ikea"])

    assert all(p.rank_basis == "relevance" for p in products)
    assert any("no best-selling sort" in w.lower() for w in warnings)


def test_weak_signal_never_outranks_a_real_bestseller_sort_at_equal_score():
    """Both normalize to 1.0 within their own site; only the basis weight
    separates them, and it must favour the site that actually sorted."""
    amazon = _p("amazon", "real bestseller", normalized_score=1.0, rank_basis="bestseller_sort")
    ikea = _p("ikea", "merely first on the page", normalized_score=1.0, rank_basis="relevance")

    ranked = _merge_and_rank([ikea, amazon])

    assert ranked[0].title == "real bestseller"
    assert RANK_BASIS_WEIGHT["bestseller_sort"] > RANK_BASIS_WEIGHT["relevance"]


# --- browser-side extraction ---------------------------------------------

def test_parses_abbreviated_sold_counts():
    assert _parse_sold("10K+ sold") == 10_000
    assert _parse_sold("1,234 sold") == 1234
    assert _parse_sold("2.5M bought") == 2_500_000
    assert _parse_sold("no demand data here") is None


def test_harvest_keeps_only_real_product_links():
    rows = [
        {"href": "https://www.temu.com/goods.html?id=1", "text": "Steel Bottle 40oz $12.99 3K+ sold", "img": "i.jpg"},
        {"href": "https://www.temu.com/channel/promo.html", "text": "Lightning deals up to 90% off today", "img": ""},
    ]
    products = harvest_to_products(rows, BROWSER_SITES["temu"])

    assert len(products) == 1
    assert products[0].title == "Steel Bottle 40oz"
    assert products[0].price_min == 12.99
    assert products[0].popularity_score == 3000


# --- Rainforest / demand-volume ranking ----------------------------------

def test_measured_sales_outrank_a_mere_sort_position():
    """A site's best-seller sort says "this came first" with no magnitude; a
    "20K+ bought in past month" figure is the actual number. The measurement
    must win — the reverse put a Walmart row with no demand data above a
    20,000-unit-a-month best seller."""
    amazon = _p("amazon", "20k sold", normalized_score=1.0, rank_basis="sold_count")
    walmart = _p("walmart", "merely sorted first", normalized_score=1.0, rank_basis="bestseller_sort")

    ranked = _merge_and_rank([walmart, amazon])

    assert ranked[0].title == "20k sold"
    assert RANK_BASIS_WEIGHT["sold_count"] > RANK_BASIS_WEIGHT["bestseller_sort"]


def test_variant_rows_of_one_product_collapse_within_a_site():
    """Rainforest returns a row per Amazon variant — same title, different
    ASIN — which stacked three identical Owala rows at the top."""
    rows = [
        _p("amazon", "Owala FreeSip 24 oz", identifier="B01", normalized_score=0.9),
        _p("amazon", "owala freesip 24 oz", identifier="B02", normalized_score=1.0),
        _p("amazon", "Owala FreeSip 24 oz", identifier="B03", normalized_score=0.8),
    ]
    ranked = _merge_and_rank(rows)

    assert len(ranked) == 1
    assert ranked[0].normalized_score == 1.0  # kept the strongest variant


def test_same_title_on_different_sites_is_not_collapsed():
    """Cross-site merging is identifier-only; collapsing by title would be the
    fuzzy matching CONTEXT.md rules out."""
    rows = [
        _p("amazon", "Owala FreeSip 24 oz", normalized_score=1.0),
        _p("walmart", "Owala FreeSip 24 oz", normalized_score=0.9),
    ]
    assert len(_merge_and_rank(rows)) == 2


def test_rainforest_maps_recent_sales_and_skips_sponsored():
    from app.rainforest import to_product

    p = to_product({
        "title": "Owala FreeSip 24 oz", "link": "https://amazon.com/x", "asin": "B085DTZQNZ",
        "rating": 4.7, "ratings_total": 131389, "recent_sales": "20K+ bought in past month",
        "price": {"value": 29.97, "currency": "USD", "raw": "$29.97"},
    })
    assert p.popularity_score == 20_000
    assert p.review_count == 131389
    assert p.identifier == "B085DTZQNZ"

    assert to_product({"title": "Ad", "link": "https://amazon.com/y", "sponsored": True}) is None


# --- Walmart price/rating reconciliation ---------------------------------

def test_walmart_cents_prices_are_normalized_to_dollars():
    """Walmart's productList mixes cents and dollars in one response; a "997.0"
    left alone showed a $997 water bottle and skewed the market snapshot."""
    from app.bestsellers import SITES, _price_fields

    walmart = SITES["walmart"]
    assert _price_fields({"price": "997.0", "currency": "USD"}, walmart)[1] == 9.97
    assert _price_fields({"price": "6068.0", "currency": "USD"}, walmart)[1] == 60.68
    # Genuinely a dollar amount — must be left alone.
    assert _price_fields({"price": "14.0", "currency": "USD"}, walmart)[1] == 14.0


def test_walmart_price_join_keys_on_item_id_not_title():
    """Titles differ between the two representations of the page (badges,
    variant wording), so the numeric item id embedded in both URLs is the only
    reliable key. It is also what excludes promo tiles, which have no /ip/ id."""
    from app.parsing.walmart_parser import item_id_from_url

    assert item_id_from_url(
        "https://www.walmart.com/ip/Mainstays-24-oz-Water-Bottle/15334974461"
    ) == "15334974461"
    assert item_id_from_url(
        "https://www.walmart.com/shop/savings/baking-essentials?athAsset=xyz"
    ) is None


def test_walmart_parser_reads_ratings_and_skips_sponsored():
    from app.parsing.walmart_parser import parse_search_results

    html = """<script id="__NEXT_DATA__" type="application/json">
    {"props":{"pageProps":{"initialData":{"searchResult":{"itemStacks":[{"items":[
      {"usItemId":"111","name":"Real Bottle","canonicalUrl":"/ip/Real/111",
       "averageRating":4.5,"numberOfReviews":627,"image":"https://i/x.jpg"},
      {"usItemId":"222","name":"Ad Bottle","canonicalUrl":"/ip/Ad/222",
       "averageRating":4.9,"numberOfReviews":5,"isSponsoredFlag":true}
    ]}]}}}}}</script>"""
    products = parse_search_results(html)

    assert len(products) == 1
    assert products[0].rating == 4.5
    assert products[0].review_count == 627
    assert products[0].identifier == "111"


def test_truncation_never_drops_a_selected_store():
    """Grouped ordering plus a plain [:TOP_N] slice let the first stores consume
    the entire budget — Amazon(40)+Walmart(41)+Temu(19) hit exactly 100 and
    Costco and IKEA vanished from a search that had selected them."""
    from app.bestsellers import TOP_N, _merge_and_rank

    rows = []
    for site, n in (("amazon", 40), ("walmart", 41), ("temu", 19), ("costco", 5), ("ikea", 4)):
        for i in range(n):
            rows.append(_p(site, f"{site}-{i}", normalized_score=1.0 - i / 100, rank_basis="relevance"))

    ranked = _merge_and_rank(rows)
    sites = {p.site for p in ranked}

    assert len(ranked) <= TOP_N
    assert sites == {"amazon", "walmart", "temu", "costco", "ikea"}
    # Every store's own best listing must survive the cut.
    for site in sites:
        assert any(p.title == f"{site}-0" for p in ranked)


def test_truncated_results_stay_grouped_by_store():
    from app.bestsellers import _merge_and_rank

    rows = [_p(s, f"{s}-{i}", normalized_score=1.0 - i / 10, rank_basis="relevance")
            for s in ("amazon", "walmart", "temu") for i in range(5)]
    ranked = _merge_and_rank(rows)

    order = [p.site for p in ranked]
    assert order == sorted(order, key=lambda s: ["amazon", "walmart", "temu"].index(s))


# --- image resolution -----------------------------------------------------

def test_thumbnail_urls_are_upgraded_to_full_resolution():
    """Every source hands back a thumbnail sized for its own grid, which looked
    blurry on our larger cards. Measured before the fix: Amazon 320px, Costco
    350px, IKEA's smallest preset. All encode size in the URL."""
    from app.product_images import upscale

    assert upscale("https://m.media-amazon.com/images/I/71x._AC_UL320_.jpg", "amazon") \
        == "https://m.media-amazon.com/images/I/71x.jpg"
    assert "f=xl" in upscale("https://www.ikea.com/x__1_pe2_s5.jpg?f=xxs", "ikea")
    assert "odnHeight=1000" in upscale("https://i5.walmartimages.com/seo/x.jpeg?odnHeight=576&odnWidth=576", "walmart")
    assert "width=1200" in upscale("https://gdx-assets.costco.com/a/b.avif?width=350&height=350&fit=contain", "costco")


def test_unrecognized_image_urls_are_left_untouched():
    """A broken high-res guess is worse than a working thumbnail."""
    from app.product_images import upscale

    url = "https://img.kwcdn.com/product/fancy/abc.jpg"
    assert upscale(url, "temu") == url
    assert upscale(None, "amazon") is None
    assert upscale("not-a-url", "amazon") == "not-a-url"


def test_ikea_ratings_parse_from_product_page_jsonld():
    """IKEA search results carry no ratings; product pages ship schema.org
    JSON-LD. (An earlier probe wrongly concluded IKEA had none — it had fetched
    a URL that redirected.)"""
    from app.product_page_enrich import parse_product_page

    html = """<script type="application/ld+json">
    {"@type":"Product","name":"ENKELSPARIG",
     "aggregateRating":{"@type":"AggregateRating","ratingValue":"3.8","reviewCount":"660"},
     "image":[{"@type":"ImageObject","contentUrl":"https://www.ikea.com/x__1_pe2_s5.jpg"}]}
    </script>"""
    data = parse_product_page(html)

    assert data["rating"] == 3.8
    assert data["review_count"] == 660
    assert data["image_url"].startswith("https://www.ikea.com/")


def test_temu_sales_and_costco_fields_map_correctly():
    from app.apify_retail import _costco_product, _parse_sales_num, _temu_product

    assert _parse_sales_num("25K+") == 25_000
    assert _parse_sales_num("1.2M+") == 1_200_000
    assert _parse_sales_num(None) is None

    temu = _temu_product({
        "title": "Insulated Bottle", "link_url": "https://www.temu.com/goods.html?goods_id=1",
        "goods_id": "601", "sales_num": "46K+",
        "price_info": {"split_price_text": ["$", "15", ".05", ""]},
        "image": {"url": "https://img.kwcdn.com/a.jpg"},
    })
    assert temu.popularity_score == 46_000
    assert temu.price_min == 15.05

    costco = _costco_product({
        "name": "ThermoFlask 40 oz", "productUrl": "https://www.costco.com/x",
        "listPrice": 32.99, "rating": 4.73, "reviewsCount": 170, "itemNumber": "2070600",
        "image": "https://gdx-assets.costco.com/a.avif?width=350&height=350",
    })
    assert costco.rating == 4.73 and costco.review_count == 170
    assert "width=1200" in costco.image_url


def test_temu_rating_comes_from_the_nested_comment_object():
    """Temu tucks rating/review count inside `comment`, not at the top level —
    reading only top-level fields left every Temu card without stars."""
    from app.apify_retail import _temu_product

    p = _temu_product({
        "title": "Insulated Bottle", "link_url": "https://www.temu.com/goods.html?goods_id=1",
        "goods_id": "601", "sales_num": "46K+",
        "comment": {"goods_score": 4.7, "comment_num_tips": "372"},
        "image": {"url": "https://img.kwcdn.com/a.jpg"},
    })
    assert p.rating == 4.7
    assert p.review_count == 372


def test_temu_abbreviated_review_counts_parse():
    """comment_num_tips is a display string using the same K/M shorthand as
    sales_num, so int() would throw away every large count."""
    from app.apify_retail import _temu_product

    p = _temu_product({
        "title": "X", "link_url": "https://www.temu.com/goods.html?goods_id=2", "goods_id": "2",
        "comment": {"goods_score": 4.9, "comment_num_tips": "1.2K"},
    })
    assert p.review_count == 1200


# ---------------------------------------------------------------------------
# Walmart's Zyte fallback: products from __NEXT_DATA__, prices from productList
# ---------------------------------------------------------------------------

WALMART_PAGE_JSON = """<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"initialData":{"searchResult":{"itemStacks":[{"items":[
  {"usItemId":"2001294100","name":"Ozark Trail 10.5\\" Cast Iron Skillet",
   "canonicalUrl":"/ip/Ozark-Trail/2001294100","averageRating":4.4,
   "numberOfReviews":1513,"price":0,"image":"https://i/a.jpg"},
  {"usItemId":"17133100116","name":"Mainstays 12 Inch Cast Iron Skillet",
   "canonicalUrl":"/ip/Mainstays/17133100116","averageRating":4.6,
   "numberOfReviews":292,"price":0,"image":"https://i/b.jpg"}
]}]}}}}}</script>"""

# What productList returns for the same search: real rows, plus the promo tiles
# Walmart mixes into the grid, plus a product the blob never mentioned.
WALMART_PRODUCT_LIST = [
    {"name": "Best seller Ozark Trail 10.5\" Cast Iron Skillet", "price": "1194.0",
     "currency": "USD", "url": "https://www.walmart.com/ip/Ozark-Trail/2001294100"},
    {"name": "Up to 35% off cookware & more", "price": "999.0", "currency": "USD",
     "url": "https://www.walmart.com/shop/savings/baking-essentials?athAsset=xyz"},
    {"name": "Kitchen & dining essentials", "price": "500.0", "currency": "USD",
     "url": "https://www.walmart.com/browse/home/dorm-kitchen-dining/4044_1"},
    {"name": "Some Skillet The Blob Did Not List", "price": "2000.0",
     "currency": "USD", "url": "https://www.walmart.com/ip/Other/9999999"},
]


class _StubZyte:
    """Stands in for ZyteClient: one method per representation of the page."""

    def __init__(self, html: str = WALMART_PAGE_JSON, items=None):
        self._html = html
        self._items = WALMART_PRODUCT_LIST if items is None else items

    async def extract_product_list(self, url, **kwargs):
        return self._items

    async def extract(self, url, **kwargs):
        import base64
        return {"httpResponseBody": base64.b64encode(self._html.encode()).decode()}


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_walmart_fallback_takes_products_from_the_page_json():
    """The blob decides which products exist and carries their ratings, so a
    row can no longer lose its stars because the two fetches disagreed."""
    from app.bestsellers import SITES, _fetch_walmart_via_zyte

    result = _run(_fetch_walmart_via_zyte(SITES["walmart"], "https://x", _StubZyte()))

    assert [p.title for p in result.products] == [
        'Ozark Trail 10.5" Cast Iron Skillet',
        "Mainstays 12 Inch Cast Iron Skillet",
    ]
    assert [p.rating for p in result.products] == [4.4, 4.6]
    assert [p.review_count for p in result.products] == [1513, 292]
    assert [p.site_rank for p in result.products] == [1, 2]


def test_walmart_fallback_drops_promo_tiles():
    """productList returns "Up to 35% off cookware & more" and category tiles as
    though they were products. They have no /ip/ item id, so keying on it
    excludes them — they were being shown as buyable rows."""
    from app.bestsellers import SITES, _fetch_walmart_via_zyte

    result = _run(_fetch_walmart_via_zyte(SITES["walmart"], "https://x", _StubZyte()))

    titles = [p.title for p in result.products]
    assert "Up to 35% off cookware & more" not in titles
    assert "Kitchen & dining essentials" not in titles
    assert all("/ip/" in p.product_url for p in result.products)


def test_walmart_fallback_prices_from_product_list_by_item_id():
    """Only productList has real prices, and the cents guard still applies to
    them — "1194.0" is $11.94."""
    from app.bestsellers import SITES, _fetch_walmart_via_zyte

    result = _run(_fetch_walmart_via_zyte(SITES["walmart"], "https://x", _StubZyte()))

    assert result.products[0].price_min == 11.94
    # The blob listed it, productList didn't — absent beats a guessed price.
    assert result.products[1].price_min is None
    assert any("show no price" in w for w in result.warnings)


def test_walmart_fallback_survives_an_unreadable_page_blob():
    """A Walmart reshuffle should cost the stars, not the site."""
    from app.bestsellers import SITES, _fetch_walmart_via_zyte

    result = _run(
        _fetch_walmart_via_zyte(SITES["walmart"], "https://x", _StubZyte(html="<html></html>"))
    )

    assert result.products, "must fall back to the productList-only shape"
    assert any("Ratings unavailable" in w for w in result.warnings)


def test_page_json_zero_price_is_absent_not_zero():
    """Walmart zeroes prices in this blob (0 of 40 carry one on a live search).
    A $0.00 row passes every "has a price" check and then drags the Market
    Snapshot median and the margin calculator to zero."""
    from app.parsing.walmart_parser import parse_search_results

    products = parse_search_results(WALMART_PAGE_JSON)
    assert all(p.price_min is None for p in products)
    assert all(p.price_text is None for p in products)
