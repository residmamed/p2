"""Google Lens (SerpApi) mapping tests — no network.

Pins the exact/visual split, since that distinction drives whether the UI shows
a green "Exact match" card or the amber closest-visual-matches fallback.
"""
from app.serp_lens import SITE_FOR_TYPE, _to_product


def test_exact_and_visual_matches_get_distinct_site_tags():
    assert SITE_FOR_TYPE["exact_matches"] == "google_lens_exact"
    assert SITE_FOR_TYPE["visual_matches"] == "google_lens"


def test_maps_a_match_onto_the_product_model():
    p = _to_product(
        {
            "title": "Owala FreeSip Stainless Steel Water Bottle",
            "link": "https://www.walmart.com/ip/owala/123",
            "thumbnail": "https://serpapi.com/thumb.jpg",
            "source": "Walmart",
        },
        "google_lens_exact",
    )
    assert p.site == "google_lens_exact"
    assert p.seller_name == "Walmart"
    assert p.product_url.startswith("https://www.walmart.com/")


def test_matches_without_a_picture_are_dropped():
    """A card with no image reads as broken in the UI — same rule the previous
    Apify-based Lens implementation used."""
    assert _to_product({"title": "No image", "link": "https://x/y"}, "google_lens") is None
    assert _to_product({"link": "https://x/y", "thumbnail": "https://t/1.jpg"}, "google_lens") is None
    assert _to_product({"title": "No link", "thumbnail": "https://t/1.jpg"}, "google_lens") is None


def test_visual_match_uses_image_when_thumbnail_absent():
    p = _to_product(
        {"title": "T", "link": "https://x/y", "image": "https://i/full.jpg"}, "google_lens"
    )
    assert p.image_url == "https://i/full.jpg"
