"""Google Shopping: keyword -> Pinterest -> Google Lens -> description gate.

The gate is the whole source. Without it this pipeline returns what Google Lens
saw in a Pinterest photograph, which for "desk lamp" is the desk, the chair, the
rug and the houseplant — all real products on real shop pages, all the wrong
answer. Every title below is from a live run.
"""
import pytest

from app import google_shopping
from app.google_shopping import SITE, describes, search
from app.models import InspirationImage, Product


# --- the description gate -------------------------------------------------

@pytest.mark.parametrize("title", [
    "Modern LED Desk Lamp with USB Charging Port",
    "Vintage Industrial Desk Lamp - Victorian Table Lamp",
    "Desk Lamps - Free Shipping",                      # plural
    "Moroccan Yellow Floral Bedside Table Lamp Desk Warm Light",
])
def test_titles_that_describe_a_desk_lamp_pass(title):
    assert describes("desk lamp", title)


@pytest.mark.parametrize("title,what", [
    ("Ergonomic Mesh Office Chair", "the chair in the same photo"),
    ("Handwoven Jute Area Rug 5x8", "the rug in the same photo"),
    ("Monstera Deliciosa Live Plant", "the plant in the same photo"),
    ("Lamp Shade For Ceiling Light", "an accessory, and not a desk one"),
    ("Standing Desk Converter, Adjustable", "a desk, but no lamp"),
])
def test_the_other_things_in_the_photograph_are_dropped(title, what):
    assert not describes("desk lamp", title), what


def test_a_two_word_keyword_requires_both_of_its_words():
    """Measured on a live run: at a bare 50% threshold a two-word keyword is
    satisfied by its head noun alone, so "desk" was never required and the grid
    filled with lamp shades, ceiling lights and floor lamps."""
    assert not describes("desk lamp", "Arc Floor Lamp, Brushed Brass")
    assert not describes("desk lamp", "Ceiling Lamp Shade, Linen")
    assert describes("desk lamp", "Adjustable Desk Lamp, Brushed Brass")


def test_the_head_noun_is_required_not_just_the_qualifiers():
    """"Stainless Steel Cutlery Set" scores two of four against "stainless steel
    water bottle" and would clear a 50% threshold while being a different
    product entirely. The last word of a product phrase is the product."""
    assert not describes("stainless steel water bottle", "Stainless Steel Cutlery Set")
    assert not describes("stainless steel water bottle", "Stainless Steel Travel Mug")
    assert describes("stainless steel water bottle", "Insulated Water Bottle 32oz")
    assert describes("stainless steel water bottle", "Owala Stainless Steel Water Bottle")


def test_a_keyword_of_only_stopwords_gates_nothing():
    """A gate with nothing to compare against must not silently empty the grid."""
    assert describes("the best", "Literally Anything At All")
    assert describes("", "Anything")


def test_a_result_with_no_title_cannot_match():
    assert not describes("desk lamp", "")


# --- the pipeline ---------------------------------------------------------

def lens_row(title, url, price=None):
    return Product(site="google_lens", title=title, product_url=url,
                   image_url="https://img.example.com/x.jpg", price_text=price)


@pytest.fixture
def wired(monkeypatch):
    """Pinterest and Lens both stubbed. Returns a dict the test can inspect."""
    state = {"lensed": []}

    async def fake_pinterest(idea, n=12):
        state["idea"], state["n"] = idea, n
        return [
            InspirationImage(image_url=f"https://i.pinimg.com/{i}.jpg", title="pin")
            for i in range(8)
        ]

    async def fake_lens(image_url):
        state["lensed"].append(image_url)
        return [
            lens_row("Modern LED Desk Lamp", f"https://shop.example.com/lamp-{image_url[-5]}", "$35.00"),
            lens_row("Ergonomic Mesh Office Chair", f"https://shop.example.com/chair-{image_url[-5]}"),
            lens_row("Desk Lamp, Brass", "https://shop.example.com/same-lamp-every-time"),
        ], []

    monkeypatch.setattr(google_shopping.pinterest, "search_pinterest", fake_pinterest)
    monkeypatch.setattr(google_shopping.serp_lens, "search_by_url", fake_lens)
    monkeypatch.setattr(google_shopping.serp_lens, "is_configured", lambda: True)
    return state


@pytest.mark.asyncio
async def test_the_chain_runs_and_only_matching_titles_survive(wired):
    products, warnings = await search("desk lamp")

    assert products, "the pipeline should return the lamps"
    # The office chair came back from every image and is in none of the results.
    assert not any("chair" in p.product_url for p in products)
    assert all(describes("desk lamp", p.title) for p in products)
    # Every row is re-tagged to this source, not the internal Lens site names.
    assert {p.site for p in products} == {SITE}
    # And says which Pinterest image found it — the only record of why it's here.
    assert all(p.inspiration_image_url for p in products)
    assert any("Dropped" in w for w in warnings)


@pytest.mark.asyncio
async def test_only_the_capped_number_of_images_is_lensed(wired):
    """Each image costs two SerpApi credits against a quota shared with Amazon,
    Walmart and the photo search, so the count is deliberately small even though
    Pinterest is asked for more (pins with no usable image waste a slot)."""
    await search("desk lamp")
    assert len(wired["lensed"]) == google_shopping.PINTEREST_IMAGES
    assert wired["n"] == google_shopping.PINTEREST_FETCH
    assert wired["n"] > google_shopping.PINTEREST_IMAGES


@pytest.mark.asyncio
async def test_the_same_shop_page_found_via_several_images_appears_once(wired):
    products, _ = await search("desk lamp")
    urls = [p.product_url for p in products]
    assert len(urls) == len(set(urls))
    assert urls.count("https://shop.example.com/same-lamp-every-time") == 1


@pytest.mark.asyncio
async def test_priced_listings_come_first(wired):
    """On a source called Shopping, a page you can buy from shouldn't sit below
    a blog post about the same lamp."""
    products, _ = await search("desk lamp")
    priced = [i for i, p in enumerate(products) if p.price_text]
    unpriced = [i for i, p in enumerate(products) if not p.price_text]
    assert priced and unpriced
    assert max(priced) < min(unpriced)


@pytest.mark.asyncio
async def test_pinterest_results_are_never_the_answer(monkeypatch, wired):
    """The chain starts at Pinterest, so a Pinterest page that Lens matched back
    to the Pinterest image we searched with is a circle, not a shop."""
    async def circular(image_url):
        return [
            lens_row("Desk Lamp Ideas - Shop on Pinterest", "https://www.pinterest.com/pin/123/"),
            lens_row("Brass Desk Lamp", "https://shop.example.com/brass"),
        ], []

    monkeypatch.setattr(google_shopping.serp_lens, "search_by_url", circular)
    products, _ = await search("desk lamp")
    assert [p.product_url for p in products] == ["https://shop.example.com/brass"]


# --- failure modes, none of which may raise -------------------------------

@pytest.mark.asyncio
async def test_no_serpapi_key_reports_instead_of_searching_pinterest(monkeypatch):
    """Lens is the second half of this source; without it the Pinterest call is
    a wasted actor run, so it isn't made."""
    called = False

    async def fake_pinterest(idea, n=12):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(google_shopping.serp_lens, "is_configured", lambda: False)
    monkeypatch.setattr(google_shopping.pinterest, "search_pinterest", fake_pinterest)

    products, warnings = await search("desk lamp")
    assert products == []
    assert not called
    assert "SERPAPI_KEY" in warnings[0]


@pytest.mark.asyncio
async def test_a_failing_pinterest_step_is_a_warning_not_an_exception(monkeypatch):
    async def boom(idea, n=12):
        raise google_shopping.pinterest.PinterestError("APIFY_TOKEN is not configured")

    monkeypatch.setattr(google_shopping.serp_lens, "is_configured", lambda: True)
    monkeypatch.setattr(google_shopping.pinterest, "search_pinterest", boom)

    products, warnings = await search("desk lamp")
    assert products == []
    assert "Pinterest step failed" in warnings[0]


@pytest.mark.asyncio
async def test_one_dead_image_does_not_sink_the_others(monkeypatch, wired):
    calls = {"n": 0}

    async def flaky(image_url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise google_shopping.serp_lens.SerpLensError("image host returned 404")
        return [lens_row("Brass Desk Lamp", f"https://shop.example.com/{calls['n']}")], []

    monkeypatch.setattr(google_shopping.serp_lens, "search_by_url", flaky)
    products, warnings = await search("desk lamp")

    assert len(products) == google_shopping.PINTEREST_IMAGES - 1
    assert any("Lens failed for one inspiration image" in w for w in warnings)


@pytest.mark.asyncio
async def test_matching_nothing_explains_why_rather_than_returning_silence(monkeypatch, wired):
    async def all_wrong(image_url):
        return [
            lens_row("Ergonomic Mesh Office Chair", "https://shop.example.com/chair"),
            lens_row("Handwoven Jute Area Rug", "https://shop.example.com/rug"),
        ], []

    monkeypatch.setattr(google_shopping.serp_lens, "search_by_url", all_wrong)
    products, warnings = await search("desk lamp")

    assert products == []
    # The distinction that matters: Lens worked, the images were just scenes.
    assert any("none of their titles describe" in w for w in warnings)


@pytest.mark.asyncio
async def test_pinterest_returning_nothing_usable_says_so(monkeypatch):
    async def no_images(idea, n=12):
        return []

    monkeypatch.setattr(google_shopping.serp_lens, "is_configured", lambda: True)
    monkeypatch.setattr(google_shopping.pinterest, "search_pinterest", no_images)

    products, warnings = await search("asdkjhaskdjh")
    assert products == []
    assert "no usable images" in warnings[0]


@pytest.mark.asyncio
async def test_output_is_capped(monkeypatch, wired):
    async def flood(image_url):
        return [
            lens_row(f"Desk Lamp model {i}", f"https://shop.example.com/{image_url[-5]}-{i}")
            for i in range(60)
        ], []

    monkeypatch.setattr(google_shopping.serp_lens, "search_by_url", flood)
    products, _ = await search("desk lamp")
    assert len(products) == google_shopping.MAX_RESULTS


# --- how it merges with the stores ---------------------------------------

def test_it_is_a_relevance_source_and_never_claims_otherwise():
    """Nothing in this chain ranks anything — Lens returns visual similarity,
    which says nothing about what sells. So it carries the lowest confidence
    weight in the cross-site merge and is labelled, not dressed up."""
    from app.bestsellers import RANK_BASIS_WEIGHT, SITES

    site = SITES["google_shopping"]
    assert site.rank_basis == "relevance"
    assert site.via_google_shopping
    # No other transport can answer this question, so there is no fallback.
    assert not site.via_apify and not site.via_api and not site.via_browser
    assert RANK_BASIS_WEIGHT["relevance"] == min(RANK_BASIS_WEIGHT.values())
