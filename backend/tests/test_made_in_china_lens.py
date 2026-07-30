"""Made-in-China as a Lens Sourcing supplier site — no network.

Added 2026-07-30. Lens turned out to serve this site far better than the count
alone suggests: over the 36 searches then in the cache it returned 19 hits,
every one of them with a real destination rather than one of the redirect
wrappers that make most `exact_matches` unusable.

Two things needed pinning. First, that a Made-in-China product page yields a
*factory* — the fixture is a live page fetched through Oxylabs, and the name it
carries ("FOSHAN WISDOM HOUSEWARE CO.,LTD") is exactly the field that would
silently regress to "Made-in-China.com" if the JSON-LD layer stopped being read.
Second, that the site's non-listing pages stay out of `results`. Lens returns
category, keyword-search and video URLs alongside listings; enriching one parses
a directory into a supplier row with a plausible title and no company behind it,
which is the confident-looking wrong answer this pipeline is built to refuse.
"""
from pathlib import Path

import pytest

from app.lens_suppliers import (
    MARKETPLACE_BY_SOURCE_LABEL,
    MAX_ENRICH,
    _candidate_from,
    _canonical,
    _dedupe,
    _enrich_targets,
    _is_product_page,
)
from app.oxylabs_client import SOURCE_FOR_SITE, site_for_url
from app.parsing.marketplace_product import parse_product_page

FIXTURE = Path(__file__).parent / "fixtures" / "made_in_china_product.html"

# Every shape observed in the live Lens cache on 2026-07-30, and whether it is
# a listing. The two `False` path styles are the ones that cost an Oxylabs call
# and produce a fictional supplier if they are let through.
LIVE_URLS = [
    ("https://m.made-in-china.com/product/New-20oz-Stainless-Steel-Mug.html", True),
    (
        "https://th-umbrella.en.made-in-china.com/product/VdNQAbaKbnkl/China-Outdoor-Umbrella.html",
        True,
    ),
    (
        "https://id.made-in-china.com/co_vivenri/product_Colors-24-Ribs-Golf-Umbrella.html",
        True,
    ),
    ("https://fr.made-in-china.com/manufacturers/dinnerware.html", False),
    (
        "https://www.made-in-china.com/products-search/hot-china-products/Small_Umbrella.html",
        False,
    ),
    (
        "https://sa.made-in-china.com/video-channel/cnplantport_JfUYNjhuVgpt_Porcelain.html",
        False,
    ),
]


def candidate(url: str, *, source: str = "Made-in-China.com", order: int = 0):
    return _candidate_from(
        {"link": url, "title": "A product", "source": source}, "lens_exact_match", order
    )


# --- routing ----------------------------------------------------------------


@pytest.mark.parametrize("url,_is_product", LIVE_URLS)
def test_every_live_url_shape_routes_to_made_in_china(url, _is_product):
    """Including the supplier-subdomain form, which has no fixed host to match."""
    assert site_for_url(url) == "made_in_china"


def test_aliexpress_is_not_mistaken_for_made_in_china():
    """The suffix test is why: a substring test for "china" catches half the web."""
    assert site_for_url("https://www.aliexpress.com/item/123.html") == "aliexpress" or (
        site_for_url("https://www.aliexpress.com/item/123.html") is None
    )
    assert site_for_url("https://made-in-china.example.com/product/x.html") is None


def test_made_in_china_has_an_oxylabs_source():
    """Without this the enrichment POST goes out with no `source` and 400s."""
    assert SOURCE_FOR_SITE["made_in_china"] == "universal"


def test_source_label_maps_when_the_url_is_a_redirect():
    """A redirect-wrapped hit has only SerpApi's label left to identify it."""
    assert MARKETPLACE_BY_SOURCE_LABEL["made-in-china.com"] == "made_in_china"


# --- which hits become supplier rows ----------------------------------------


@pytest.mark.parametrize("url,is_product", LIVE_URLS)
def test_only_listings_are_treated_as_products(url, is_product):
    assert _is_product_page(candidate(url)) is is_product


def test_products_search_is_not_read_as_a_product_path():
    """`/products-search/` contains the word and is a keyword search, which is
    the entire reason the test is for `/product/` and `/product_`."""
    assert (
        _is_product_page(
            candidate("https://www.made-in-china.com/products-search/hot/Mug.html")
        )
        is False
    )


def test_other_sites_are_not_shape_tested():
    """Alibaba returns product URLs or nothing; a shape guess would drop rows."""
    assert _is_product_page(candidate("https://www.alibaba.com/anything.html")) is True


# --- dedupe -----------------------------------------------------------------


def test_locale_subdomains_collapse():
    """The same listing served in French and Spanish is one Oxylabs call, not
    two — each duplicate that survives costs a real fetch."""
    assert _canonical("https://fr.made-in-china.com/co_x/product_Mug.html") == _canonical(
        "https://es.made-in-china.com/co_x/product_Mug.html"
    )


def test_supplier_subdomains_do_not_collapse():
    """Two factories, not one listing seen twice. Collapsing the company out of
    the host would merge their listings and report one supplier for both."""
    assert _canonical(
        "https://wisdom.en.made-in-china.com/product/aaa/Mug.html"
    ) != _canonical("https://th-umbrella.en.made-in-china.com/product/bbb/Mug.html")


def test_dedupe_keeps_one_of_a_locale_pair():
    kept = _dedupe(
        [
            candidate("https://fr.made-in-china.com/co_x/product_Mug.html", order=0),
            candidate("https://ru.made-in-china.com/co_x/product_Mug.html", order=1),
        ]
    )
    assert len(kept) == 1


# --- the enrichment budget --------------------------------------------------


def _alibaba(n: int):
    return [
        candidate(f"https://www.alibaba.com/product-detail/x_{i}.html", order=i)
        for i in range(n)
    ]


def _mic(n: int):
    return [
        candidate(
            f"https://co{i}.en.made-in-china.com/product/x{i}/Mug.html",
            source="Made-in-China.com",
            order=100 + i,
        )
        for i in range(n)
    ]


def test_a_high_volume_site_cannot_take_the_whole_budget():
    """The shape of a real search, measured 2026-07-30: 55 Alibaba hits and 3
    Made-in-China. Taking the first ten of the Lens ordering gave Alibaba all
    ten slots and every Made-in-China row came back with no supplier and no MOQ.
    """
    picked = _enrich_targets(_alibaba(55) + _mic(3))
    sites = [c.marketplace for c in picked]
    assert len(picked) == MAX_ENRICH
    assert sites.count("made_in_china") == 3
    assert sites.count("alibaba") == MAX_ENRICH - 3


def test_the_budget_is_not_split_evenly_when_one_site_is_short():
    """A site with fewer hits than its share does not hold slots it cannot use —
    the remainder goes back to whoever has listings left."""
    picked = _enrich_targets(_alibaba(20) + _mic(1))
    assert len(picked) == MAX_ENRICH
    assert [c.marketplace for c in picked].count("alibaba") == MAX_ENRICH - 1


def test_the_best_matching_row_of_each_site_is_taken_first():
    """Dealt by round, so each site contributes its strongest Lens match before
    any site contributes its second."""
    picked = _enrich_targets(_alibaba(5) + _mic(5))
    assert picked[0].product_url.endswith("x_0.html")
    assert picked[1].product_url.endswith("x0/Mug.html")


def test_sites_with_no_enrichment_step_are_not_dealt_slots():
    """Taobao rows are returned but never enriched; giving them a slot would
    spend it on a page nothing here can read."""
    taobao = [candidate("https://world.taobao.com/item/1.htm", order=0)]
    picked = _enrich_targets(taobao + _alibaba(3))
    assert [c.marketplace for c in picked] == ["alibaba"] * 3


# --- what the product page actually yields ----------------------------------


@pytest.fixture(scope="module")
def parsed():
    return parse_product_page(FIXTURE.read_text(encoding="utf-8"), "made_in_china")


def test_the_factory_is_named_not_the_marketplace(parsed):
    """JSON-LD `brand` is the factory here, unlike Alibaba where it is the site.
    A regression to "Made-in-China.com" is the failure worth catching."""
    assert parsed.supplier_name == "FOSHAN WISDOM HOUSEWARE CO.,LTD"


def test_the_company_name_is_clickable(parsed):
    """The site ships no `companyProfileUrl` blob, so without the com-name
    anchor the supplier arrives named but with nothing behind it."""
    assert parsed.supplier_url == "https://wisdomhouseware.en.made-in-china.com"


def test_price_is_read_with_its_currency(parsed):
    assert parsed.price_min == 2.92
    assert parsed.currency == "USD"


def test_the_sites_own_merchandising_flag_is_stripped(parsed):
    """og:title arrives as "[Hot Item] 40oz ..." — the site talking about its
    own listing, not part of the product's name."""
    assert parsed.title.startswith("40oz Double Wall Vacuum")
    assert "[Hot Item]" not in parsed.title


def test_the_page_is_not_read_as_empty(parsed):
    """`is_empty()` is what sends a row back on its Lens data with an error."""
    assert not parsed.is_empty()
