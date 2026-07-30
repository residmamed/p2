"""Apify retail-actor mapping tests for the six stores added after Temu/Costco.

Every payload below is trimmed from a real 5-item probe of the actor named in
the test, so what's pinned is what those actors actually sent — not what their
READMEs claim. The cases worth having tests at all are the ones where the actor
and the app disagree about what a field means, because those fail silently:

  * Etsy quotes a price that isn't a number ("19.9919" for a $19.99 listing).
  * Etsy publishes a rating with no review count, which must stay None rather
    than becoming 0 — a 0 would read as "nobody liked it".
  * eBay's only rating-shaped fields describe the seller, not the product, so
    the product's rating must stay empty.
  * Best Buy nests the price one level down and reports a blocked run as a
    dataset record instead of an error.
  * Target's top best-seller is routinely the one row with no price.
"""
import pytest

from app.apify_retail import ACTORS, _etsy_price, _is_error_record, fetch_site

TARGET_ITEM = {
    "tcin": "1012180040",
    "title": "Owala Stainless Steel FreeSip Water Bottle",
    "price": None,
    "priceString": "",
    "regularPrice": None,
    "rating": 4.7,
    "reviewCount": 17969,
    "brand": "Owala",
    "thumbnail": "https://target.scene7.com/is/image/Target/GUEST_65d67247-cabd-4bba-8f21-e0abcd2aa440",
    "url": "https://www.target.com/p/owala-water-bottle/-/A-1012180040",
    "isSponsored": False,
}

EBAY_ITEM = {
    "type": "product",
    "itemId": "293788683996",
    "title": "Collapsible BPA-free Silicone Water Bottle, Foldable Travel Friendly",
    "price": 9.99,
    "priceString": "$9.99",
    "soldCount": "",
    "sellerName": "enableconnections",
    "sellerFeedbackPercent": "99.5%",
    "sellerFeedbackCount": "1.6K",
    "isSponsored": False,
    "thumbnail": "https://i.ebayimg.com/images/g/83IAAeSw6lZo9GMy/s-l500.webp",
    "url": "https://www.ebay.com/itm/293788683996",
}

ETSY_ITEM = {
    "listingId": "4375573490",
    "name": "Custom Tumbler 24 oz Engraved Sip Dupe Water Bottle",
    "url": "https://www.etsy.com/listing/4375573490/custom-tumbler-24-oz",
    "imageUrl": "https://i.etsystatic.com/44786246/r/il/0d1f9e/8094148584/il_300x300.8094148584_1gf7.jpg",
    "shop": "",
    "price": "19.9919",
    "currency": "USD",
    "rating": 5,
    "position": 1,
}

HOMEDEPOT_ITEM = {
    "itemId": "342937904",
    "url": "https://www.homedepot.com/p/Milwaukee-M18-FUEL-Rotary-Hammer/342937904",
    "title": "M18 FUEL 18V Lithium-Ion Brushless Cordless SDS-Plus Rotary Hammer",
    "brand": "Milwaukee",
    "price": 549,
    "currency": "USD",
    "imageUrl": "https://images.thdstatic.com/productImages/48c6528c/svn/milwaukee-rotary-hammers-64_600.jpg",
}

BESTBUY_ITEM = {
    "sku": "6501017",
    "name": "Beats - Studio Pro - Wireless Noise Cancelling Over-the-Ear Headphones",
    "priceDomain": {"regularPrice": 349.99, "currentPrice": 199.99, "totalSavings": 150},
    "imageUrl": "https://pisces.bbystatic.com/image2/BestBuy_US/images/products/6501/6501017_sd.jpg",
    "productUrl": "https://www.bestbuy.com/product/beats-studio-pro/6501017",
    "rating": 4.7,
    "reviewsCount": 15356,
}

WAYFAIR_ITEM = {
    "sku": "W112123741",
    "name": "Skillern LED Desk Lamps, Eye-Caring Swing Arm Table Lamp",
    "url": "https://www.wayfair.com/lighting/pdp/symple-stuff-skillern-led-desk-lamps.html",
    "price": 38.99,
    "previousPrice": None,
    "currency": "USD",
    "rating": 4.8,
    "reviewCount": 737,
    "leadImage": "https://assets.wfcdn.com/im/23934343/resize-h600-w600%5Ecompr-r85/3422/342264074/lamp.jpg",
}

# Best Buy's actor reports a run the site blocked as an ordinary dataset record.
BESTBUY_BLOCKED = {
    "type": "bestbuy_error",
    "reason": "no_results",
    "message": "Best Buy returned no parseable product records. This usually means the "
               "bot-challenge persisted across all retries.",
    "keyword": "wireless headphones",
}


def parse(site, item):
    return ACTORS[site].to_product(item)


# --- the fields each store does and does not publish ----------------------

def test_target_keeps_its_best_seller_even_with_no_price():
    """Target prices multi-variant listings per variant, so the #1 best seller
    is routinely the row with price=None. Dropping it would remove the single
    most important result on the page."""
    product = parse("target", TARGET_ITEM)
    assert product is not None
    assert product.price_min is None and product.price_text is None
    # The signal Target is ranked on survives regardless.
    assert (product.rating, product.review_count) == (4.7, 17969)
    assert product.identifier == "1012180040"
    assert product.seller_name == "Target"


def test_ebay_seller_feedback_is_not_a_product_rating():
    """eBay rates sellers, not products. sellerFeedbackPercent ("99.5%") in the
    rating column would present a seller's lifetime score as this item's
    rating, which is a different claim entirely."""
    product = parse("ebay", EBAY_ITEM)
    assert product.rating is None
    assert product.review_count is None
    # The seller name is real per-listing data on eBay, unlike the store label
    # every other site in this module gets.
    assert product.seller_name == "enableconnections"
    assert product.price_min == 9.99


def test_etsy_price_recovers_from_the_actors_doubled_digits():
    """The actor concatenates the price with a repeat of its leading digits."""
    assert _etsy_price("19.9919") == 19.99
    assert _etsy_price("29.5029") == 29.50
    assert _etsy_price("3.803") == 3.80
    assert _etsy_price("7") == 7.0       # a bare integer is already correct
    assert _etsy_price(None) is None
    assert _etsy_price("") is None


def test_etsy_rating_travels_without_a_fabricated_review_count():
    product = parse("etsy", ETSY_ITEM)
    assert product.rating == 5.0
    # Etsy sends no review count. It must stay absent: a 0 would read as a real
    # measurement, and the UI's review-weighted rating sort would then rank this
    # unbacked 5.0 against ratings that have thousands of reviews behind them.
    assert product.review_count is None
    assert product.price_min == 19.99
    # shop was "" — an empty string is not a seller name.
    assert product.seller_name == "Etsy"


def test_homedepot_publishes_price_but_no_rating():
    product = parse("homedepot", HOMEDEPOT_ITEM)
    assert product.price_min == 549.0
    assert product.price_text == "$549.00"
    assert product.rating is None and product.review_count is None


def test_bestbuy_price_comes_from_the_nested_current_price():
    """priceDomain carries both; currentPrice is what a buyer actually pays."""
    product = parse("bestbuy", BESTBUY_ITEM)
    assert product.price_min == 199.99   # not the 349.99 regularPrice
    assert (product.rating, product.review_count) == (4.7, 15356)
    assert product.identifier == "6501017"


def test_a_zero_rating_means_unrated_not_terrible():
    """Seen live from Best Buy: rating 0.0 with reviewCount 0. These stores rate
    on 1-5 stars, so 0 is "no reviews yet" — keeping it would place an unrated
    product below every genuinely bad one and pull the market average down."""
    product = parse("bestbuy", {**BESTBUY_ITEM, "rating": 0.0, "reviewsCount": 0})
    assert product is not None
    assert product.rating is None
    assert product.review_count is None
    # A real rating that happens to sit alongside no reviews is left alone.
    kept = parse("bestbuy", {**BESTBUY_ITEM, "rating": 4.9, "reviewsCount": 0})
    assert kept.rating == 4.9


def test_wayfair_maps_every_field_it_sends():
    product = parse("wayfair", WAYFAIR_ITEM)
    assert product.price_min == 38.99
    assert (product.rating, product.review_count) == (4.8, 737)
    assert product.identifier == "W112123741"


# --- rows that must not become products -----------------------------------

def test_sponsored_rows_are_dropped():
    """A paid placement holding a Site Rank would put an ad above an organic
    best seller."""
    assert parse("target", {**TARGET_ITEM, "isSponsored": True}) is None


@pytest.mark.parametrize("site,item", [
    ("target", TARGET_ITEM),
    ("ebay", EBAY_ITEM),
    ("etsy", ETSY_ITEM),
    ("homedepot", HOMEDEPOT_ITEM),
    ("bestbuy", BESTBUY_ITEM),
    ("wayfair", WAYFAIR_ITEM),
])
def test_a_row_with_no_title_or_no_link_is_dropped(site, item):
    title_key = next(k for k in ("title", "name") if k in item)
    url_key = next(k for k in ("url", "productUrl") if k in item)
    assert parse(site, {**item, title_key: ""}) is None
    assert parse(site, {**item, url_key: ""}) is None


def test_blocked_run_record_is_recognised():
    assert _is_error_record(BESTBUY_BLOCKED)
    assert not _is_error_record(BESTBUY_ITEM)
    # It must never be mapped into a titleless product.
    assert parse("bestbuy", BESTBUY_BLOCKED) is None


@pytest.mark.asyncio
async def test_blocked_run_reports_the_sites_own_message(monkeypatch):
    """The generic "no usable products" line would blame the parser for a bot
    challenge. The actor's own explanation is more useful than ours."""
    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return [BESTBUY_BLOCKED]

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **kw):
            return FakeResponse()

    monkeypatch.setattr("app.apify_retail.httpx.AsyncClient", lambda **kw: FakeClient())
    monkeypatch.setattr("app.apify_retail.settings", type("S", (), {"apify_token": "t"})())

    products, warnings = await fetch_site("bestbuy", "wireless headphones")
    assert products == []
    assert len(warnings) == 1
    assert "bot-challenge" in warnings[0]
    assert warnings[0].startswith("[Best Buy]")


# --- the inputs these actors are actually sent ----------------------------

def test_each_store_asks_for_the_strongest_ordering_it_offers():
    """The rank_basis in bestsellers.py is only honest if the input asks for the
    sort it claims. Target and Home Depot expose a real best-selling order; the
    other four expose none, and must not pretend otherwise."""
    assert ACTORS["target"].build_input("q", 40)["sort"] == "bestselling"
    assert ACTORS["homedepot"].build_input("q", 40)["sortBy"] == "top_sellers"
    # No best-selling option exists in these actors' enums.
    assert ACTORS["ebay"].build_input("q", 40)["sort"] == "best_match"
    assert ACTORS["etsy"].build_input("q", 40)["sort"] == "most_relevant"
    assert "sort" not in ACTORS["bestbuy"].build_input("q", 40)
    assert "sort" not in ACTORS["wayfair"].build_input("q", 40)


def test_start_url_actors_get_the_shape_each_one_accepts():
    """Neither actor accepts the other's startUrls shape: Best Buy takes plain
    strings, Wayfair takes {"url": ...} objects. Both were probed live."""
    bestbuy = ACTORS["bestbuy"].build_input("desk lamp", 40)["startUrls"]
    assert bestbuy == ["https://www.bestbuy.com/site/searchpage.jsp?st=desk+lamp"]

    wayfair = ACTORS["wayfair"].build_input("desk lamp", 40)["startUrls"]
    assert wayfair == [{"url": "https://www.wayfair.com/keyword.php?keyword=desk+lamp"}]


def test_an_ordinary_search_is_held_to_one_page():
    """These actors bill per product scraped, so a second page doubles the cost
    of every search to fill in rows below the fold. Only the per-store "find
    more" button asks for more than an ordinary search's worth — see
    test_find_more.py for the paging that opens up then."""
    from app.apify_retail import DEFAULT_MAX_ITEMS

    assert ACTORS["target"].build_input("q", DEFAULT_MAX_ITEMS)["maxSearchPages"] == 1
    assert ACTORS["ebay"].build_input("q", DEFAULT_MAX_ITEMS)["maxSearchPages"] == 1
