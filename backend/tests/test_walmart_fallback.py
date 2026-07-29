"""Walmart's Zyte fallback path — the source of "no reviews, no prices".

Walmart is served by SerpApi first; this path only runs when that is
unavailable, which is why the gaps went unnoticed. Both defects below were
measured against live searches, and both are about reading data that was
already on the page.
"""

import json

from app.parsing import walmart_parser as wp


def blob_html(items: list[dict]) -> str:
    data = {"props": {"pageProps": {"initialData": {"searchResult": {"itemStacks": [{"items": items}]}}}}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'


def item(**kw) -> dict:
    base = {
        "usItemId": "13679666130",
        "name": "TAL 26oz Stainless Steel Ranger Water Bottle",
        "canonicalUrl": "/ip/TAL-26oz/13679666130",
        "price": 0,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Ratings — the blob ships them in two shapes
# ---------------------------------------------------------------------------


def test_rating_read_from_the_flat_fields():
    products = wp.parse_search_results(blob_html([item(averageRating=4.5, numberOfReviews=5068)]))
    assert (products[0].rating, products[0].review_count) == (4.5, 5068)


def test_rating_read_from_the_nested_object():
    """Measured live: the flat field was on 32 of 42 items, the nested object on
    40. Reading only the flat pair dropped the stars off a fifth of the rows."""
    products = wp.parse_search_results(
        blob_html([item(rating={"averageRating": 4.6, "numberOfReviews": 1010})])
    )
    assert (products[0].rating, products[0].review_count) == (4.6, 1010)


def test_flat_field_wins_when_both_are_present():
    products = wp.parse_search_results(
        blob_html([item(averageRating=4.5, numberOfReviews=10, rating={"averageRating": 1.0, "numberOfReviews": 2})])
    )
    assert (products[0].rating, products[0].review_count) == (4.5, 10)


def test_unrated_product_has_no_stars():
    """Walmart writes 0 for 'not rated yet' — not a one-star product."""
    products = wp.parse_search_results(blob_html([item(averageRating=0, rating={"averageRating": 0})]))
    assert products[0].rating is None


# ---------------------------------------------------------------------------
# Prices — the blob has none, so they come off the rendered grid
# ---------------------------------------------------------------------------

TILE = """
<html><body>
  <div data-item-id="3U00UQACJJI9">
    <a href="/ip/TAL-26oz/13679666130">TAL 26oz</a>
    <span>In 200+ people's carts</span><span>$10.97</span>
  </div>
  <div data-item-id="1GMLFW9MEX5N">
    <a href="/ip/Great-Value-Water/22222222">Great Value Water</a>
    <span>$0.00</span>
  </div>
  <div data-item-id="NOLINK"><span>$5.00</span></div>
</body></html>
"""


def test_prices_are_read_off_the_rendered_grid():
    """The blob zeroes every price and the grid fills them in client side, so
    the rendered page is the only place the blob's own products carry one."""
    prices = wp.prices_from_dom(TILE)
    assert prices["13679666130"] == 10.97


def test_zero_price_is_not_a_price():
    """$0.00 is Walmart's 'pick a store first' placeholder. A zero sails through
    every has-a-price check and then zeroes the Market Snapshot median."""
    prices = wp.prices_from_dom(TILE)
    assert "22222222" not in prices


def test_tile_without_a_product_link_is_skipped():
    assert len(wp.prices_from_dom(TILE)) == 1


def test_no_prices_on_an_empty_page():
    assert wp.prices_from_dom("<html><body></body></html>") == {}


def test_productlist_zero_price_is_dropped_too():
    """The grid path already refuses a zero; productList is the other half of
    this fallback and was passing `"price": "0"` straight through as $0.00."""
    from app.bestsellers import SITES, _price_fields

    assert _price_fields({"price": "0", "currencyRaw": "$"}, SITES["walmart"]) == (None, None, "$")
    assert _price_fields({"price": "10.97", "currencyRaw": "$"}, SITES["walmart"])[1] == 10.97
