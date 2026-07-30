"""Lens Sourcing (POST /api/find-suppliers) — no network.

The behaviours pinned here are the ones whose failure would be invisible: a row
silently dropped, a Lens hit routed to the wrong parser, an Oxylabs auth failure
arriving as an ordinary empty result. A wrong supplier name is a bad answer that
looks like a good one, which is the class of bug this file exists to catch.
"""
import asyncio
import time

import pytest

from app import lens_cache, lens_suppliers
from app.lens_suppliers import (
    FindSuppliersError,
    LensCandidate,
    _candidate_from,
    _canonical,
    _dedupe,
    _enrich,
    decode_image_base64,
)
from app.models import PriceRange
from app.oxylabs_client import (
    OxylabsAuthError,
    OxylabsClient,
    OxylabsError,
    site_for_url,
)
from app.parsing.marketplace_product import _money, parse_product_page


def candidate(url: str, *, order: int = 0, confidence: str = "lens_visual_match", **kwargs):
    from app.oxylabs_client import site_for_url as which

    defaults = dict(
        title="A product",
        product_url=url,
        source_domain=url.split("/")[2],
        marketplace=which(url),
        image_url="https://serpapi.example/thumb.jpg",
        price_text="$3.99",
        source_name="Alibaba.com",
        match_confidence=confidence,
        order=order,
    )
    defaults.update(kwargs)
    return LensCandidate(**defaults)


# --- domain routing ---------------------------------------------------------


def test_marketplace_is_matched_by_hostname_not_substring():
    """AliExpress is a consumer marketplace on a different domain. A substring
    test for "alibaba" catches it, sends it to the Alibaba B2B parser, and
    reports a retail price as a factory quote."""
    assert site_for_url("https://www.alibaba.com/product-detail/x_123.html") == "alibaba"
    assert site_for_url("https://spanish.alibaba.com/product-detail/x_123.html") == "alibaba"
    assert site_for_url("https://detail.1688.com/offer/98765.html") == "1688"
    assert site_for_url("https://item.taobao.com/item.htm?id=1") == "taobao"

    assert site_for_url("https://www.aliexpress.com/item/1005.html") is None
    assert site_for_url("https://alibaba.com.evil.example/product") is None
    assert site_for_url("https://www.amazon.com/dp/B01?q=alibaba.com") is None
    assert site_for_url("not a url at all") is None


def test_dedupe_keeps_exact_matches_ahead_and_collapses_locale_duplicates():
    """Lens returns the same listing once per locale subdomain and once per
    tracking parameter; every survivor costs a real Oxylabs call."""
    kept = _dedupe(
        [
            candidate("https://www.alibaba.com/product-detail/mug_1.html?spm=a1", order=3),
            candidate("https://spanish.alibaba.com/product-detail/mug_1.html", order=1),
            candidate(
                "https://detail.1688.com/offer/55.html",
                order=0,
                confidence="lens_exact_match",
            ),
        ]
    )
    assert [c.product_url for c in kept] == [
        "https://detail.1688.com/offer/55.html",
        "https://spanish.alibaba.com/product-detail/mug_1.html",
    ]


def test_canonical_ignores_scheme_query_and_trailing_slash():
    a = _canonical("http://www.alibaba.com/product-detail/x_1.html/?spm=zzz")
    b = _canonical("https://www.alibaba.com/product-detail/x_1.html")
    assert a == b


# --- Google's exact-match redirect wrapper ----------------------------------
# Measured 2026-07-29: every `type=exact_matches` row comes back as
# lens.google.com/goto?url=<encrypted token>. The token holds no plaintext URL
# and the wrapper 404s on a server-side fetch, so the destination is unknowable
# without a browser. These tests pin that it degrades honestly instead of
# quietly producing supplier rows nobody can open.

GOTO = "https://lens.google.com/goto?url=CAESjQEB7keqTSrQzvjPaHf"


def test_a_redirect_wrapped_hit_is_marked_unresolvable():
    row = _candidate_from(
        {"title": "Ceramic mug", "link": GOTO, "source": "Alibaba.com"},
        "lens_exact_match",
        0,
    )
    assert row.resolvable is False
    # The site label still identifies the marketplace — enough to report the
    # hit exists, never enough to link to it.
    assert row.marketplace == "alibaba"


def test_aliexpress_source_label_is_not_read_as_alibaba():
    row = _candidate_from(
        {"title": "x", "link": GOTO, "source": "AliExpress"}, "lens_exact_match", 0
    )
    assert row.marketplace is None


def test_a_direct_link_is_resolvable_and_classified_by_url():
    row = _candidate_from(
        {
            "title": "x",
            "link": "https://detail.1688.com/offer/1.html",
            "source": "Amazon.com",  # deliberately wrong: the URL must win
        },
        "lens_visual_match",
        0,
    )
    assert (row.resolvable, row.marketplace) == (True, "1688")


def test_redirect_wrappers_dedupe_on_their_token_not_their_path():
    """Every wrapper shares the path /goto. Dropping the query — which is what
    the canonical form does for real URLs — would collapse forty distinct exact
    matches into one."""
    kept = _dedupe(
        [
            candidate(GOTO + "AAA", order=0, marketplace="alibaba", resolvable=False),
            candidate(GOTO + "BBB", order=1, marketplace="alibaba", resolvable=False),
            candidate(GOTO + "AAA", order=2, marketplace="alibaba", resolvable=False),
        ]
    )
    assert len(kept) == 2


@pytest.mark.asyncio
async def test_unreachable_marketplace_hits_are_reported_not_returned_as_results(monkeypatch):
    """The failure this guards against: Lens finds the product on Alibaba, we
    cannot follow the link, and the response says "no suppliers found" — which
    reads as "nobody makes this"."""

    async def fake_lens(_url):
        return [
            candidate(GOTO + "AAA", order=0, marketplace="alibaba", resolvable=False,
                      confidence="lens_exact_match"),
            candidate("https://www.amazon.com/dp/B01", order=1, marketplace=None),
        ], []

    monkeypatch.setattr(lens_suppliers, "_run_lens", fake_lens)
    response = await lens_suppliers.find_suppliers(
        image_url="https://example.com/photo.jpg", use_cache=False
    )

    assert response.results == []
    assert [p.product_url for p in response.partial_matches][0] == GOTO + "AAA"
    assert any("behind a redirect" in w for w in response.warnings)
    assert any("alibaba" in w for w in response.warnings)


@pytest.mark.asyncio
async def test_a_reachable_marketplace_hit_becomes_a_result(monkeypatch):
    url = "https://www.alibaba.com/product-detail/mug_9.html"

    async def fake_lens(_url):
        return [candidate(url, order=0), candidate("https://www.amazon.com/dp/B01", order=1)], []

    monkeypatch.setattr(lens_suppliers, "_run_lens", fake_lens)
    response = await lens_suppliers.find_suppliers(
        image_url="https://example.com/photo.jpg",
        oxylabs=FakeOxylabs(pages={url: ALIBABA_PAGE}),
        use_cache=False,
    )

    assert [r.product_url for r in response.results] == [url]
    assert response.results[0].enriched is True
    assert [p.source_domain for p in response.partial_matches] == ["www.amazon.com"]
    assert response.step_timings.lens_ms >= 0
    assert response.query_image == "https://example.com/photo.jpg"


# --- input handling ---------------------------------------------------------


def test_base64_accepts_both_a_data_url_and_a_bare_payload():
    assert decode_image_base64("aGVsbG8=") == b"hello"
    assert decode_image_base64("data:image/png;base64,aGVsbG8=") == b"hello"


def test_bad_base64_is_a_clear_error_not_an_empty_result():
    with pytest.raises(FindSuppliersError, match="not valid base64"):
        decode_image_base64("!!!! not base64 !!!!")
    with pytest.raises(FindSuppliersError, match="zero bytes"):
        decode_image_base64("")


@pytest.mark.asyncio
async def test_no_image_is_rejected_before_any_api_call():
    with pytest.raises(FindSuppliersError, match="No image provided"):
        await lens_suppliers.find_suppliers()
    with pytest.raises(FindSuppliersError, match="not both"):
        await lens_suppliers.find_suppliers(image_url="https://x/y.jpg", image_base64="aGk=")


# --- enrichment fallback ----------------------------------------------------


class FakeOxylabs(OxylabsClient):
    """Stands in for the Web Scraper API. `pages` maps URL -> HTML; anything
    absent raises whatever `error` says."""

    def __init__(self, pages=None, error=None, configured=True):
        super().__init__(username="u", password="p")
        self.pages = pages or {}
        self.error = error
        self._configured = configured
        self.calls: list[str] = []

    def is_configured(self):
        return self._configured

    async def scrape(self, url, site=None, client=None):
        self.calls.append(url)
        if url in self.pages:
            return self.pages[url]
        raise self.error or OxylabsError("no such page")


ALIBABA_PAGE = """
<html><head>
  <meta property="og:title" content="Wholesale Ceramic Coffee Mug 350ml">
  <meta property="og:image" content="//sc04.alicdn.com/kf/mug.jpg">
  <script type="application/ld+json">
  {"@type":"Product","name":"Wholesale Ceramic Coffee Mug 350ml",
   "offers":{"@type":"AggregateOffer","lowPrice":"1.20","highPrice":"2.50",
             "priceCurrency":"USD"}}
  </script>
  <script>window.__data = {"companyName":"Yongkang Baimuyu Industries And Trading Co., Ltd.",
    "minOrderQuantity":100,"productUnit":"pieces"};</script>
</head><body><div>Min. Order: 100 Pieces</div></body></html>
"""


@pytest.mark.asyncio
async def test_enrichment_fills_supplier_price_and_moq():
    url = "https://www.alibaba.com/product-detail/mug_1.html"
    rows, warnings, errors = await _enrich(
        [candidate(url)], FakeOxylabs(pages={url: ALIBABA_PAGE})
    )
    assert not errors
    (row,) = rows
    assert row.enriched is True
    assert row.supplier_name == "Yongkang Baimuyu Industries And Trading Co., Ltd."
    assert row.product_title == "Wholesale Ceramic Coffee Mug 350ml"
    assert row.moq == "100 pieces"
    assert isinstance(row.price, PriceRange)
    assert (row.price.min, row.price.max, row.price.currency) == (1.20, 2.50, "USD")
    assert row.match_confidence == "lens_visual_match"


@pytest.mark.asyncio
async def test_a_failed_page_keeps_the_row_on_its_lens_data():
    """The brief's central rule: don't drop a candidate silently when
    enrichment fails — degrade it, and say why on the row."""
    url = "https://www.alibaba.com/product-detail/mug_2.html"
    rows, warnings, errors = await _enrich(
        [candidate(url, title="Lens title")],
        FakeOxylabs(error=OxylabsError("Oxylabs timed out after 8s.")),
    )
    (row,) = rows
    assert row.product_url == url
    assert row.enriched is False
    assert row.product_title == "Lens title"
    assert row.price == "$3.99"
    assert "timed out" in row.enrichment_error
    assert any("could not be read" in w for w in warnings)


@pytest.mark.asyncio
async def test_auth_failure_is_an_error_not_a_silent_empty_result():
    url = "https://www.alibaba.com/product-detail/mug_3.html"
    rows, warnings, errors = await _enrich(
        [candidate(url)], FakeOxylabs(error=OxylabsAuthError("rejected the credentials"))
    )
    assert len(rows) == 1 and rows[0].enriched is False
    assert errors and "OXYLABS_USERNAME" in errors[0]


@pytest.mark.asyncio
async def test_unconfigured_oxylabs_degrades_with_a_warning_and_no_calls():
    url = "https://www.alibaba.com/product-detail/mug_4.html"
    fake = FakeOxylabs(configured=False)
    rows, warnings, errors = await _enrich([candidate(url)], fake)
    assert fake.calls == []
    assert len(rows) == 1 and rows[0].enriched is False
    assert not errors  # a missing key is a config state, not an operator fault
    assert any("not configured" in w for w in warnings)


@pytest.mark.asyncio
async def test_taobao_is_returned_unenriched_rather_than_dropped():
    url = "https://item.taobao.com/item.htm?id=99"
    rows, _, _ = await _enrich([candidate(url)], FakeOxylabs())
    (row,) = rows
    assert row.source == "taobao"
    assert row.enriched is False
    assert "no Oxylabs enrichment step" in row.enrichment_error


@pytest.mark.asyncio
async def test_candidates_past_the_cap_survive_as_lens_rows():
    urls = [f"https://www.alibaba.com/product-detail/x_{i}.html" for i in range(15)]
    fake = FakeOxylabs(pages={u: ALIBABA_PAGE for u in urls})
    rows, _, _ = await _enrich([candidate(u, order=i) for i, u in enumerate(urls)], fake)

    assert len(rows) == 15, "no candidate may be dropped by the enrichment cap"
    assert len(fake.calls) == lens_suppliers.MAX_ENRICH
    assert sum(1 for r in rows if r.enriched) == lens_suppliers.MAX_ENRICH
    assert all("past the" in r.enrichment_error for r in rows if not r.enriched)


@pytest.mark.asyncio
async def test_a_challenge_page_is_reported_not_parsed_into_a_fake_supplier():
    url = "https://www.alibaba.com/product-detail/mug_5.html"
    rows, warnings, _ = await _enrich(
        [candidate(url)],
        FakeOxylabs(pages={url: "<html><body>Please verify you are human</body></html>"}),
    )
    (row,) = rows
    assert row.enriched is False
    assert row.supplier_name is None
    assert "published none of the fields" in row.enrichment_error


@pytest.mark.asyncio
async def test_enrichment_runs_concurrently_not_serially():
    """Six 50ms pages must finish in well under 300ms, or the 5s budget is
    being spent one page at a time."""
    urls = [f"https://www.alibaba.com/product-detail/c_{i}.html" for i in range(6)]

    class SlowOxylabs(FakeOxylabs):
        async def scrape(self, url, site=None, client=None):
            await asyncio.sleep(0.05)
            return ALIBABA_PAGE

    started = time.perf_counter()
    rows, _, _ = await _enrich([candidate(u) for u in urls], SlowOxylabs())
    elapsed = time.perf_counter() - started

    assert all(r.enriched for r in rows)
    assert elapsed < 0.2, f"took {elapsed:.3f}s — enrichment is serializing"


# --- product page parsing ---------------------------------------------------


def test_parser_prefers_published_standards_over_scraped_text():
    parsed = parse_product_page(ALIBABA_PAGE, "alibaba")
    assert parsed.title == "Wholesale Ceramic Coffee Mug 350ml"
    assert parsed.image_url == "https://sc04.alicdn.com/kf/mug.jpg"
    assert (parsed.price_min, parsed.price_max) == (1.20, 2.50)
    assert parsed.price_text == "$1.20 - $2.50"


def test_parser_reads_a_chinese_moq_off_a_1688_page():
    html = """
    <html><head><meta property="og:title" content="陶瓷马克杯 批发"></head>
    <body><span>起订量 2件</span><span>¥8.50</span></body></html>
    """
    parsed = parse_product_page(html, "1688")
    assert parsed.moq == "2件"
    assert parsed.price_min == 8.50
    assert parsed.currency == "CNY"


# --- what four live Alibaba product pages taught us, 2026-07-29 -------------


def test_a_delisted_listing_yields_nothing_despite_valid_structured_data():
    """The trap: a dead Alibaba listing answers 200 with well-formed JSON-LD
    claiming name "Product Not Available", brand "Alibaba", price 0.99 and
    availability InStock. Nothing about its shape says it is dead, and quoting
    $0.99 for a product that no longer exists is the worst output this pipeline
    could produce."""
    dead = """
    <html><head><title>Product Not Available</title>
    <meta property="og:title" content="Product Not Available">
    <script type="application/ld+json">
    {"@type":"Product","name":"Product Not Available",
     "brand":{"@type":"Brand","name":"Alibaba"},
     "offers":{"@type":"Offer","price":"0.99","priceCurrency":"USD",
               "availability":"http://schema.org/InStock"}}
    </script></head><body></body></html>
    """
    assert parse_product_page(dead, "alibaba").is_empty()


def test_the_marketplace_is_never_reported_as_the_supplier():
    html = """
    <html><head><meta property="og:title" content="A real mug">
    <script type="application/ld+json">
    {"@type":"Product","name":"A real mug","brand":{"@type":"Brand","name":"Alibaba"}}
    </script></head><body></body></html>
    """
    assert parse_product_page(html, "alibaba").supplier_name is None


def test_the_sites_seo_suffix_is_stripped_from_the_title():
    html = (
        '<html><head><meta property="og:title" content="Mini 2oz Tumbler Steel '
        '- Buy  Product on Alibaba.com"></head><body>x</body></html>'
    )
    assert parse_product_page(html, "alibaba").title == "Mini 2oz Tumbler Steel"


LADDER_PAGE = """
<html><head><meta property="og:title" content="Brand 30oz Tumbler">
<script type="application/ld+json">
{"@type":"Product","name":"Brand 30oz Tumbler",
 "brand":{"@type":"Brand","name":"Hefei Ekocian Metal Products Co., Ltd."},
 "offers":{"@type":"Offer","price":"2.99","priceCurrency":"USD"}}
</script>
<script>window.d={"priceList":[
 {"minQuantity":24,"formatPrice":"$3.99"},
 {"minQuantity":200,"formatPrice":"$3.59"},
 {"minQuantity":5000,"formatPrice":"$2.99"}],
 "formatMinOrderQuantity":"24 pieces"};</script>
</head><body>x</body></html>
"""


def test_the_quantity_ladder_beats_json_lds_single_price():
    """JSON-LD advertises 2.99 — the rate at 5,000 units. The MOQ is 24, where
    this supplier charges 3.99. Publishing "$2.99, MOQ 24 pieces" is not a
    rounding error, it is the wrong number."""
    parsed = parse_product_page(LADDER_PAGE, "alibaba")
    assert (parsed.price_min, parsed.price_max, parsed.currency) == (2.99, 3.99, "USD")
    assert parsed.price_text == "$2.99 - $3.99"
    assert parsed.moq == "24 pieces"
    assert parsed.supplier_name == "Hefei Ekocian Metal Products Co., Ltd."


def test_a_ladder_in_a_localised_currency_keeps_that_currency():
    """Alibaba localises by exit IP: the same URL returned Serbian dinar from
    one exit and dollars from another. Value and symbol must travel together —
    taking the number from the ladder and the currency from JSON-LD's "USD"
    reports $452.71 for a $2.99 tumbler."""
    page = LADDER_PAGE.replace('"$3.99"', '"RSD\\u00a0452.71"').replace(
        '"$3.59"', '"RSD\\u00a0407.33"'
    ).replace('"$2.99"', '"RSD\\u00a0339.25"')
    parsed = parse_product_page(page, "alibaba")
    assert parsed.currency == "RSD"
    assert (parsed.price_min, parsed.price_max) == (339.25, 452.71)


def test_a_ladder_that_mixes_currencies_is_discarded_not_reconciled():
    page = LADDER_PAGE.replace('"$3.59"', '"€3.59"')
    parsed = parse_product_page(page, "alibaba")
    # Falls back to JSON-LD's single price rather than spanning two currencies.
    assert parsed.price_min == parsed.price_max == 2.99


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The bug this guards: Alibaba renders prices in the visitor's locale
        # and half the world writes the decimal point as a comma. A live run
        # returned a rung of "9,84" (with "MOQ 10 Adet" and "MOQ 2 개" giving
        # the locale away) and the old `replace(",", "")` read it as 984 — a
        # hundredfold error on a supplier quote, shown with a dollar sign, in
        # the table a buyer picks a factory from.
        ("9,84", 9.84),
        ("28,52", 28.52),
        ("452,71", 452.71),
        ("0,99", 0.99),
        # Both separators present: whichever comes last is the decimal one.
        ("1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        # A 3-digit tail after a lone comma is the grouping convention.
        ("1,234", 1234.0),
        ("1.234.567", 1234567.0),
        ("452.71", 452.71),
        ("3.99", 3.99),
        ("12", 12.0),
    ],
)
def test_numbers_are_parsed_in_whatever_locale_the_page_used(raw, expected):
    from app.parsing.marketplace_product import _as_float

    assert _as_float(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        # The site localises the MOQ unit independently of the currency: one
        # live response carried "$7.65 / 2 개" and "$9.84 / 10 Adet" together.
        ("2 개", "2 pieces"),
        ("10 Adet", "10 pieces"),
        ("5 Stück", "5 pieces"),
        ("24 pieces", "24 pieces"),
        ("2.0 pieces", "2 pieces"),
        # Not pieces — a genuinely different quantity, left alone.
        ("600 sets", "600 sets"),
        ("1 carton", "1 carton"),
        # Unreadable and unrecognised: keep the count, drop the word.
        ("3 个件箱", "3"),
        ("100", "100"),
    ],
)
def test_moq_units_are_normalised_so_a_table_reads_consistently(raw, expected):
    from app.parsing.marketplace_product import _normalize_moq

    assert _normalize_moq(raw) == expected


def test_a_comma_decimal_ladder_does_not_inflate_the_price_a_hundredfold():
    """End-to-end version of the above, through the ladder that feeds the UI."""
    page = LADDER_PAGE.replace('"$3.99"', '"$3,99"').replace(
        '"$3.59"', '"$3,59"'
    ).replace('"$2.99"', '"$2,99"')
    parsed = parse_product_page(page, "alibaba")
    assert (parsed.price_min, parsed.price_max) == (2.99, 3.99)
    assert parsed.currency == "USD"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$3.99", (3.99, "USD")),
        # Local shorthand these sites print instead of ISO codes. Requiring
        # three letters dropped the rung, and with it the whole ladder.
        ("R\xa028,52", (28.52, "ZAR")),
        ("162.96 TL", (162.96, "TRY")),
        ("€2.93", (2.93, "EUR")),
        ("RSD\xa0452.71", (452.71, "RSD")),
        ("US $1.20", (1.20, "USD")),
        ("¥8.50", (8.50, "CNY")),
        ("1,234.56 EUR", (1234.56, "EUR")),
        # No nameable currency -> no price. Defaulting to USD is how a
        # 452-dinar quote becomes a $452 one.
        ("12.00", (None, None)),
        ("free", (None, None)),
    ],
)
def test_money_takes_value_and_currency_from_the_same_string(text, expected):
    assert _money(text) == expected


def test_an_empty_page_yields_nothing_rather_than_a_guess():
    assert parse_product_page("", "alibaba").is_empty()
    assert parse_product_page("<html><body></body></html>", "alibaba").is_empty()


def test_a_malformed_page_does_not_raise():
    """A parser exception must cost one row, never the batch."""
    assert parse_product_page("<<<not html", "alibaba") is not None


# --- cache ------------------------------------------------------------------


def test_cache_round_trips_and_expires_after_thirty_days(tmp_path, monkeypatch):
    monkeypatch.setattr(lens_cache.settings, "lens_cache_dir", str(tmp_path))
    key = lens_cache.key_for_bytes(b"some image bytes")

    assert lens_cache.get(key) is None
    lens_cache.put(key, {"candidates": [{"title": "x"}]})
    assert lens_cache.get(key) == {"candidates": [{"title": "x"}]}
    assert lens_cache.age_seconds(key) < 5

    # 29 days on: still inside the TTL the brief asks for.
    now = time.time()
    monkeypatch.setattr(lens_cache.time, "time", lambda: now + 29 * 86400)
    assert lens_cache.get(key) == {"candidates": [{"title": "x"}]}

    # 31 days on: a miss, and the stale file is cleared on the way out rather
    # than left for a reaper this app doesn't have.
    monkeypatch.setattr(lens_cache.time, "time", lambda: now + 31 * 86400)
    assert lens_cache.get(key) is None
    assert list(tmp_path.glob("*.json")) == []


def test_uploads_and_urls_key_differently_but_deterministically():
    assert lens_cache.key_for_bytes(b"abc") == lens_cache.key_for_bytes(b"abc")
    assert lens_cache.key_for_bytes(b"abc") != lens_cache.key_for_bytes(b"abd")
    assert lens_cache.key_for_url("https://x/y.jpg").startswith("url:")
    assert lens_cache.key_for_bytes(b"abc").startswith("sha256:")


def test_a_corrupt_cache_file_reads_as_a_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(lens_cache.settings, "lens_cache_dir", str(tmp_path))
    key = lens_cache.key_for_url("https://x/y.jpg")
    lens_cache.put(key, {"candidates": []})
    next(tmp_path.glob("*.json")).write_text("{ this is not json", encoding="utf-8")
    assert lens_cache.get(key) is None


# --- serialization round trip ----------------------------------------------


def test_cached_candidates_survive_a_round_trip():
    original = [candidate("https://www.alibaba.com/product-detail/x_1.html", order=2)]
    restored = lens_suppliers._deserialize(lens_suppliers._serialize(original))
    assert restored == original


def test_a_cache_entry_from_an_older_shape_is_skipped_not_fatal():
    restored = lens_suppliers._deserialize({"candidates": [{"title": "only a title"}]})
    assert restored == []
