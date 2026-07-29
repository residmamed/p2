"""Zyte product-page enrichment for Google Lens results — no network.

Lens returns a title, a link and a picture; measured on a live search, a price
on 11 of 65 results and a rating on none. These pin the mapping that fills the
rest in from the product page.
"""
from app.models import Product
from app.product_page_enrich import (
    _is_retail_host,
    _price_number,
    apply_zyte_product,
)


def _lens_result(**kwargs) -> Product:
    base = dict(site="google_lens_exact", title="Owala Kids FreeSip 16oz",
                product_url="https://www.target.com/p/owala/-/A-1")
    return Product(**{**base, **kwargs})


def test_fills_price_rating_and_reviews_from_the_product_page():
    p = _lens_result()
    assert apply_zyte_product(p, {
        "metadata": {"probability": 0.999},
        "price": "24.99",
        "currencyRaw": "$",
        "aggregateRating": {"ratingValue": 4.8, "reviewCount": 14230},
        "mainImage": {"url": "https://target.scene7.com/is/image/x?wid=1200"},
    }) is True
    assert p.price_min == 24.99 and p.price_max == 24.99
    assert p.price_text == "$24.99"
    assert p.rating == 4.8 and p.review_count == 14230


def test_page_price_replaces_the_image_searchs_cached_one():
    """Lens's price comes from Google's cached snippet; Zyte reads the live
    page. Where they disagree the page wins."""
    p = _lens_result(price_text="$31.99")
    apply_zyte_product(p, {"metadata": {"probability": 0.99}, "price": "24.97", "currencyRaw": "$"})
    assert p.price_min == 24.97 and p.price_text == "$24.97"


def test_cached_price_still_becomes_a_number_when_the_page_has_none():
    """A price the workbench can't do arithmetic on is barely a price — the
    Market Snapshot and margin maths both need price_min."""
    p = _lens_result(price_text="$31.99")
    apply_zyte_product(p, {"metadata": {"probability": 0.99}})
    assert p.price_min == 31.99


def test_low_confidence_extraction_is_rejected_outright():
    """Zyte returns a best-guess product for 404s and category redirects; the
    confidence score is the only tell. A guessed price is worse than none."""
    p = _lens_result()
    assert apply_zyte_product(p, {"metadata": {"probability": 0.05}, "price": "9.99"}) is False
    assert p.price_min is None and p.price_text is None


def test_existing_rating_is_never_overwritten():
    p = _lens_result(rating=4.2, review_count=17)
    apply_zyte_product(p, {
        "metadata": {"probability": 0.99},
        "aggregateRating": {"ratingValue": 1.0, "reviewCount": 99999},
    })
    assert p.rating == 4.2 and p.review_count == 17


def test_review_count_without_a_rating_value_is_still_kept():
    """Measured on Target: reviewCount 14230 with no ratingValue. Half the data
    is still data."""
    p = _lens_result()
    apply_zyte_product(p, {
        "metadata": {"probability": 0.99}, "aggregateRating": {"reviewCount": 14230},
    })
    assert p.review_count == 14230 and p.rating is None


def test_only_cross_site_identifiers_are_taken():
    """gtin/mpn identify a product anywhere; sku is private to one site and
    would merge unrelated listings under the identifier-only merge rule."""
    p = _lens_result()
    apply_zyte_product(p, {"metadata": {"probability": 0.99}, "gtin": "00193575011158", "sku": "A-1"})
    assert p.identifier == "gtin:00193575011158"

    q = _lens_result()
    apply_zyte_product(q, {"metadata": {"probability": 0.99}, "sku": "A-1"})
    assert q.identifier is None


def test_social_and_video_hosts_are_not_worth_a_request():
    """16 of 65 results on a measured Lens search — no price or rating to find."""
    for url in (
        "https://www.tiktok.com/@x/video/123",
        "https://www.instagram.com/p/abc/",
        "https://www.facebook.com/marketplace/item/1",
        "https://m.youtube.com/watch?v=1",
    ):
        assert _is_retail_host(url) is False
    for url in ("https://www.target.com/p/x", "https://www.ebay.com/itm/1", "https://qfc.com/p/y"):
        assert _is_retail_host(url) is True


def test_price_number_handles_the_shapes_these_sites_publish():
    assert _price_number("$24.99") == 24.99
    assert _price_number("US $31.99") == 31.99
    assert _price_number("1,299.00") == 1299.0
    assert _price_number("") is None
    assert _price_number(None) is None
    assert _price_number("Sold out") is None


# ---------------------------------------------------------------------------
# Which pages the request budget is spent on
# ---------------------------------------------------------------------------

def _candidate(site: str, i: int) -> Product:
    return Product(site=site, title=f"{site}-{i}",
                   product_url=f"https://www.target.com/p/{site}-{i}")


def _budgeted(n_exact: int, n_visual: int):
    from app.product_page_enrich import _budget
    chosen = _budget(
        [_candidate("google_lens_exact", i) for i in range(n_exact)]
        + [_candidate("google_lens", i) for i in range(n_visual)]
    )
    exact = sum(1 for p in chosen if p.site == "google_lens_exact")
    return exact, len(chosen) - exact


def test_visual_matches_keep_a_reserved_slice_of_the_budget():
    """The UI shows every exact match, or — when there are none — the closest
    handful of visual ones. A Lens search returns ~25 exact hits, which would
    otherwise eat the whole budget and leave the fallback grid rendering the
    only cards the user sees with no price and no rating."""
    from app.product_page_enrich import PRODUCT_PAGE_LIMIT, VISUAL_MATCH_RESERVE

    exact, visual = _budgeted(25, 40)
    assert exact + visual == PRODUCT_PAGE_LIMIT
    assert visual == VISUAL_MATCH_RESERVE


def test_reserved_slots_are_not_wasted_when_one_group_is_short():
    """A reserve that idles is just a smaller budget."""
    from app.product_page_enrich import PRODUCT_PAGE_LIMIT

    assert _budgeted(0, 40) == (0, PRODUCT_PAGE_LIMIT)
    assert _budgeted(40, 0) == (PRODUCT_PAGE_LIMIT, 0)
    # Fewer candidates than the budget: take them all, no padding.
    assert _budgeted(3, 2) == (3, 2)
    assert _budgeted(30, 3) == (PRODUCT_PAGE_LIMIT - 3, 3)
