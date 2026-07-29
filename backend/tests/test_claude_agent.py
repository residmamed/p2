"""The contracts that matter for the Claude agents are the failure paths.

A filter that deletes rows is only safe if every way it can go wrong resolves
towards keeping them. These tests pin that down: no key, a dead batch, a model
that skips an index, and a thumbnail that won't load must all leave the listing
visible. The happy paths are tested too, but they are the easy half.

Every test stubs the network — `_ask` and the thumbnail fetch — so the suite
stays offline and free.
"""

import asyncio

import pytest

from app import claude_agent, sourcing
from app.claude_agent import VisionVerdict
from app.models import Product, SourcingResult


def product(title: str, site: str = "amazon", image_url: str | None = None) -> Product:
    return Product(
        title=title,
        product_url=f"https://example.com/{title.replace(' ', '-')}",
        site=site,
        image_url=image_url,
    )


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(claude_agent.settings, "anthropic_api_key", "sk-test")
    return True


# ---------------------------------------------------------------------------
# Relevance agent
# ---------------------------------------------------------------------------


def test_relevance_drops_accessories_and_unrelated(monkeypatch, configured):
    async def fake_ask(content, schema, system, effort, max_tokens):
        return {
            "verdicts": [
                {"i": 0, "verdict": "match"},
                {"i": 1, "verdict": "accessory"},
                {"i": 2, "verdict": "unrelated"},
                {"i": 3, "verdict": "variant"},
            ]
        }

    monkeypatch.setattr(claude_agent, "_ask", fake_ask)
    products = [
        product("Stanley Quencher Tumbler 40oz"),
        product("Replacement Lid for 40oz Tumbler"),
        product("Yoga Mat 6mm"),
        product("Tumbler + Straw Gift Set"),
    ]

    outcome = asyncio.run(claude_agent.filter_by_relevance("tumbler", products))

    assert [p.title for p in outcome.kept] == [
        "Stanley Quencher Tumbler 40oz",
        "Tumbler + Straw Gift Set",
    ]
    assert len(outcome.dropped) == 2
    # The verdict rides on the row so the UI can explain the absence.
    assert products[1].relevance == "accessory"
    assert any("Hid 2 listing" in w for w in outcome.warnings)
    # The count must be reported as-is, never quietly topped back up.
    assert any("not padded back up" in w for w in outcome.warnings)


def test_relevance_without_key_keeps_everything(monkeypatch):
    monkeypatch.setattr(claude_agent.settings, "anthropic_api_key", "")

    async def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("no key means no API call")

    monkeypatch.setattr(claude_agent, "_ask", explode)
    products = [product("A"), product("B")]

    outcome = asyncio.run(claude_agent.filter_by_relevance("anything", products))

    assert outcome.kept == products
    assert outcome.dropped == []
    assert outcome.warnings == []
    assert products[0].relevance is None  # unscreened, not "found relevant"


def test_relevance_failed_batch_keeps_rows_and_says_so(monkeypatch, configured):
    async def fake_ask(*args, **kwargs):
        raise RuntimeError("upstream timeout")

    monkeypatch.setattr(claude_agent, "_ask", fake_ask)
    products = [product("A"), product("B")]

    outcome = asyncio.run(claude_agent.filter_by_relevance("tumbler", products))

    assert outcome.kept == products
    assert any("unscreened" in w for w in outcome.warnings)


def test_relevance_missing_verdict_keeps_the_row(monkeypatch, configured):
    """A model that answers about 2 of 3 rows has not rejected the third."""

    async def fake_ask(content, schema, system, effort, max_tokens):
        return {"verdicts": [{"i": 0, "verdict": "match"}, {"i": 2, "verdict": "unrelated"}]}

    monkeypatch.setattr(claude_agent, "_ask", fake_ask)
    products = [product("A"), product("B"), product("C")]

    outcome = asyncio.run(claude_agent.filter_by_relevance("tumbler", products))

    assert [p.title for p in outcome.kept] == ["A", "B"]
    assert products[1].relevance is None


def test_relevance_batches_large_result_sets(monkeypatch, configured):
    """100 rows must not become 100 calls."""
    calls = []

    async def fake_ask(content, schema, system, effort, max_tokens):
        text = content[0]["text"]
        indices = [
            int(line.split(".", 1)[0])
            for line in text.splitlines()
            if line and line[0].isdigit()
        ]
        calls.append(len(indices))
        return {"verdicts": [{"i": i, "verdict": "match"} for i in indices]}

    monkeypatch.setattr(claude_agent, "_ask", fake_ask)
    products = [product(f"Tumbler {i}") for i in range(100)]

    outcome = asyncio.run(claude_agent.filter_by_relevance("tumbler", products))

    assert len(outcome.kept) == 100
    assert len(calls) == 3  # 40 + 40 + 20
    assert sum(calls) == 100


# ---------------------------------------------------------------------------
# Vision agent
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_images(monkeypatch):
    """Stub the thumbnail download and the encoder — no network, no PIL."""

    async def fake_fetch(client, semaphore, url):
        return None if "missing" in url else b"bytes"

    monkeypatch.setattr(claude_agent, "_fetch_thumb", fake_fetch)
    monkeypatch.setattr(
        claude_agent,
        "_encode_image",
        lambda data: {"type": "image", "source": {"type": "base64"}} if data else None,
    )


def test_vision_judges_and_reports_unfetchable(monkeypatch, configured, fake_images):
    async def fake_ask(content, schema, system, effort, max_tokens):
        return {
            "verdicts": [
                {"i": 0, "verdict": "same_product", "confidence": 0.9, "note": "same handle"},
                {"i": 2, "verdict": "different", "confidence": 0.95, "note": "a bicycle"},
            ]
        }

    monkeypatch.setattr(claude_agent, "_ask", fake_ask)
    products = [
        product("Mug", "alibaba", "https://cdn/1.jpg"),
        product("Broken thumb", "1688", "https://cdn/missing.jpg"),
        product("Bike", "alibaba", "https://cdn/3.jpg"),
    ]

    verdicts, warnings = asyncio.run(
        claude_agent.verify_supplier_matches(b"query", products)
    )

    assert verdicts[0].verdict == "same_product"
    assert verdicts[0].tier == "exact"
    assert verdicts[2].tier is None  # "different" carries no tier
    assert 1 not in verdicts
    assert any("wouldn't load" in w for w in warnings)


def test_vision_without_key_is_a_no_op(monkeypatch):
    monkeypatch.setattr(claude_agent.settings, "anthropic_api_key", "")
    products = [product("Mug", "alibaba", "https://cdn/1.jpg")]

    verdicts, warnings = asyncio.run(
        claude_agent.verify_supplier_matches(b"query", products)
    )

    assert verdicts == {}
    assert warnings == []


# ---------------------------------------------------------------------------
# Sourcing integration — what the verdicts do to the grid
# ---------------------------------------------------------------------------


def result(title: str, site: str, tier: str = "unverified") -> SourcingResult:
    return SourcingResult(
        product=product(title, site, image_url=f"https://cdn/{title}.jpg"),
        match_tier=tier,
        match_basis="phash",
    )


def test_vision_drops_different_and_leaves_unjudged_alone(monkeypatch, configured):
    """Verdict indices address the *candidate* list the agent was handed, which
    is re-ordered by site — so the mapping back is keyed off the products that
    were actually sent, not off the original result order."""

    async def fake_verify(image_bytes, products):
        by_title = {p.title: i for i, p in enumerate(products)}
        return (
            {
                by_title["Tumbler A"]: VisionVerdict("same_product", 0.9, "same ribbing"),
                by_title["Bike B"]: VisionVerdict("different", 0.95, "a bicycle"),
            },
            [],
        )

    monkeypatch.setattr(claude_agent, "verify_supplier_matches", fake_verify)
    results = [
        result("Tumbler A", "alibaba"),
        result("Bike B", "alibaba"),
        result("Tumbler C", "1688"),
    ]

    kept, warnings = asyncio.run(sourcing._apply_vision_verdicts(results, b"query"))

    titles = [r.product.title for r in kept]
    assert "Bike B" not in titles  # rejected by the judge
    assert "Tumbler C" in titles  # never judged -> never rejected

    promoted = next(r for r in kept if r.product.title == "Tumbler A")
    assert promoted.match_tier == "exact"
    assert promoted.match_basis == "vision"
    assert promoted.match_note == "same ribbing"

    untouched = next(r for r in kept if r.product.title == "Tumbler C")
    assert untouched.match_tier == "unverified"
    assert untouched.match_basis == "phash"

    assert any("different product" in w for w in warnings)


def test_phash_identical_survives_a_weaker_vision_tier(monkeypatch, configured):
    """A reused image file is harder evidence than a visual judgement."""

    async def fake_verify(image_bytes, products):
        return {0: VisionVerdict("same_category", 0.5, "looks similar")}, []

    monkeypatch.setattr(claude_agent, "verify_supplier_matches", fake_verify)
    results = [result("Tumbler A", "alibaba", tier="identical")]

    kept, _ = asyncio.run(sourcing._apply_vision_verdicts(results, b"query"))

    assert kept[0].match_tier == "identical"
    assert kept[0].match_basis == "vision"


def test_vision_candidates_are_dealt_across_sites(monkeypatch):
    """The cap must not be spent entirely on whichever site sorted first."""
    monkeypatch.setattr(claude_agent, "VISION_TOP_N", 4)
    results = (
        [result(f"A{i}", "alibaba") for i in range(10)]
        + [result(f"M{i}", "made_in_china") for i in range(10)]
    )

    picked = sourcing._vision_candidates(results)

    assert len(picked) == 4
    assert {r.product.site for r in picked} == {"alibaba", "made_in_china"}


def test_vision_ranks_verified_rows_above_hash_only_peers():
    verified = result("Verified", "alibaba", tier="exact")
    verified.match_basis = "vision"
    verified.match_confidence = 0.9
    hashed = result("Hash only", "alibaba", tier="exact")

    assert sourcing._rank_key(verified) < sourcing._rank_key(hashed)
