"""SerpApi retail mapping tests — no network.

The payloads below are trimmed from real responses. They pin the two things
that actually went wrong on the path this replaced: reading a sale item's price
off the wrong field, and losing the cents on a three-digit price.
"""
from app.serpapi_retail import ENGINE_PARAMS, QUERY_PARAM, _amazon_product, _walmart_product


def test_both_engines_ask_for_the_sites_own_bestselling_sort():
    """Site Rank is only a real ranking if the request carried the sort. Losing
    either of these would silently turn best sellers back into page order."""
    assert ENGINE_PARAMS["amazon"]["s"] == "exact-aware-popularity-rank"
    assert ENGINE_PARAMS["walmart"]["sort"] == "best_seller"
    assert QUERY_PARAM == {"amazon": "k", "walmart": "query"}


def test_walmart_uses_the_offer_price_not_the_struck_through_one():
    """The Zyte path reported a $10.99 item as $16.99 by reading the was-price.
    offer_price is what you pay today and the only field ever used."""
    p = _walmart_product(
        {
            "title": "Hawsaiy 12oz Kids Insulated Water Bottle",
            "product_page_url": "https://www.walmart.com/ip/hawsaiy/5445972026",
            "thumbnail": "https://i5.walmartimages.com/x.jpeg?odnHeight=180&odnWidth=180",
            "rating": 4.6,
            "reviews": 336,
            "us_item_id": "5445972026",
            "seller_name": "Walmart.com",
            "primary_offer": {"offer_price": 10.99, "was_price": 16.99, "currency": "USD"},
        }
    )
    assert p.price_min == 10.99
    assert p.price_text == "$10.99"
    assert p.rating == 4.6 and p.review_count == 336
    # A real Shared Identifier, which the Zyte path only got from a second fetch.
    assert p.identifier == "5445972026"
    assert p.seller_name == "Walmart.com"


def test_walmart_three_digit_price_survives_intact():
    """`249.0` from Zyte plus the cents heuristic listed a $249.99 item at
    $2.49. A typed offer_price has no such failure mode — this pins that."""
    p = _walmart_product(
        {
            "title": "Samsung Galaxy Buds4 Pro",
            "product_page_url": "https://www.walmart.com/ip/buds/1",
            "primary_offer": {"offer_price": 249.99, "currency": "USD"},
        }
    )
    assert p.price_min == 249.99
    assert p.price_text == "$249.99"


def test_walmart_listing_without_an_offer_has_no_price_rather_than_a_guess():
    p = _walmart_product(
        {"title": "Out of stock item", "product_page_url": "https://www.walmart.com/ip/x/2"}
    )
    assert p.price_min is None and p.price_text is None


def test_a_zero_offer_price_is_absent_not_free():
    """Walmart won't price every row it returns — out of stock, or price shown
    only in the cart — and SerpApi renders that as `offer_price: 0`. Measured on
    one live `laptop` search: 1 of 40 rows, which reached the card as "$0.00"
    and pulled the Market Snapshot's median down with it."""
    p = _walmart_product(
        {
            "title": 'Lenovo IdeaPad Slim 3i 15.6" Laptop',
            "product_page_url": "https://www.walmart.com/ip/lenovo/3",
            "primary_offer": {"offer_price": 0, "min_price": 0},
        }
    )
    assert p.price_min is None and p.price_text is None

    a = _amazon_product(
        {"title": "Unpriced", "link": "https://a/9", "extracted_price": 0, "price": "$0.00"}
    )
    assert a.price_min is None and a.price_text is None


def test_walmart_falls_back_to_min_price_when_only_that_is_published():
    """Variant listings ("from $12.99") carry min_price without an offer_price.
    A price the shopper will actually be charged at least is worth showing."""
    p = _walmart_product(
        {
            "title": "Tumbler, 6 colors",
            "product_page_url": "https://www.walmart.com/ip/tumbler/4",
            "primary_offer": {"min_price": 12.99},
        }
    )
    assert p.price_min == 12.99 and p.price_text == "$12.99"


def test_amazon_carries_purchase_volume_rating_and_asin():
    p = _amazon_product(
        {
            "title": "Owala FreeSip Stainless Steel Water Bottle 24 oz",
            "link": "https://www.amazon.com/dp/B085DTZQNZ?tag=x",
            "link_clean": "https://www.amazon.com/dp/B085DTZQNZ",
            "thumbnail": "https://m.media-amazon.com/images/I/71x._AC_UL320_.jpg",
            "asin": "B085DTZQNZ",
            "rating": 4.7,
            "reviews": 131400,
            "extracted_price": 29.97,
            "price": "$29.97",
            "bought_last_month": "20K+ bought in past month",
        }
    )
    # "20K+ bought in past month" is the strongest demand signal in the app.
    assert p.popularity_score == 20000
    assert p.identifier == "B085DTZQNZ"
    assert p.price_min == 29.97
    # The tracking-free URL is preferred where the payload offers one.
    assert p.product_url == "https://www.amazon.com/dp/B085DTZQNZ"
    # Thumbnails are upgraded to full resolution — see product_images.py.
    assert "_AC_UL320_" not in p.image_url


def test_sponsored_rows_are_excluded_from_both_sites():
    """A paid placement is not a demand signal; ranking one as a best seller is
    the failure this app exists to avoid."""
    assert _amazon_product(
        {"title": "Ad", "link": "https://a/1", "sponsored": True, "asin": "X"}
    ) is None
    assert _walmart_product(
        {"title": "Ad", "product_page_url": "https://w/1", "sponsored": True}
    ) is None


def test_rows_missing_a_title_or_url_are_dropped():
    assert _amazon_product({"link": "https://a/1"}) is None
    assert _amazon_product({"title": "No link"}) is None
    assert _walmart_product({"product_page_url": "https://w/1"}) is None
    assert _walmart_product({"title": "No link"}) is None
