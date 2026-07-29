"""Claude agents that judge *what a result actually is*, which none of the
existing signals can do.

Three agents, each answering one question the rest of the pipeline gets wrong:

    RELEVANCE   "is this listing the product the user searched for?"
                Retail sites answer a keyword with their whole long tail —
                lids, cases, replacement straws, cleaning brushes, and outright
                unrelated stock. bestsellers.py ranked all of it, so a search
                for `tumbler` shipped a hundred rows of which maybe forty were
                tumblers. Nothing in a rating, a sold count or a page position
                distinguishes a tumbler from a tumbler lid; only reading the
                title does. See `filter_by_relevance`.

    INTENT      "what would a right answer even look like?"
                Folded into the same call as RELEVANCE (`_INTENT_RULE`) rather
                than run separately — the model needs the query's meaning to
                judge the titles anyway, and two round trips to establish it
                would double the latency of every search.

    VISUAL      "is this supplier listing the same product as this photo?"
                sourcing.py's docstring records the measurement that motivates
                this one: on a live run, *every* real match between an Amazon
                studio photo and Made-in-China catalogue photos landed past
                perceptual-hash distance 20. phash recognises a reused image
                file, not a re-shot product, so the sourcing grid could only
                ever say "unverified" for the normal case. A vision model
                compares the products rather than the pixels. See
                `verify_supplier_matches`.

Every agent degrades the same way the scrapers do: no key, a bad key, a
timeout, or a malformed reply means the caller gets its input back untouched
plus a warning saying so. An unavailable judge must never empty the grid — the
failure mode of a filter is silent deletion, and that is worse than the
unfiltered list it was meant to improve.

Two things are deliberately *not* done here:
  * Nothing is invented. The agents only ever return verdicts about rows that
    already came from a real site; no titles, prices or suppliers are
    generated.
  * Nothing is back-filled. When a search drops to 12 relevant rows, the answer
    is 12 rows — the TOP_N budget in bestsellers.py is a ceiling, never a quota
    to pad out with near-misses.
"""

import asyncio
import base64
import json
from dataclasses import dataclass, field
from io import BytesIO

import httpx

from .config import settings
from .models import Product

try:  # The SDK is optional at import time so a missing install degrades like a missing key.
    import anthropic
except ImportError:  # pragma: no cover - exercised only on an incomplete install
    anthropic = None


# Batch size for the title classifier. Big enough that a 100-row search is 3
# calls rather than 100, small enough that one malformed reply can't cost the
# whole result set.
RELEVANCE_BATCH = 40

# Vision is the expensive agent — every candidate is a downloaded, re-encoded
# image in the request body. Judge the rows a user will actually reach; the
# rest keep their perceptual-hash tier and say so.
VISION_TOP_N = 20
VISION_BATCH = 10
VISION_THUMB_PX = 384

REQUEST_TIMEOUT_SECONDS = 90.0
IMAGE_FETCH_TIMEOUT_SECONDS = 8.0
IMAGE_FETCH_CONCURRENCY = 8

# Same rationale as image_match.FETCH_HEADERS: supplier CDNs 403 a default
# httpx UA. The images cannot be handed to the API as URLs for exactly this
# reason — they have to be fetched here, with browser-ish headers, and inlined.
FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

# Verdicts the relevance agent may return, and whether a row survives it.
KEEP_VERDICTS = {"match", "variant"}
DROP_VERDICTS = {"accessory", "unrelated"}

# Verdicts the vision agent may return, mapped onto sourcing.py's match tiers.
VISION_TIER = {
    "same_product": "exact",
    "same_model_variant": "exact",
    "same_category": "similar",
    "different": None,  # None => not the product; sourcing drops it
}


def is_configured() -> bool:
    return bool(settings.anthropic_api_key) and anthropic is not None


def _client():
    return anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=2,
    )


async def _ask(
    content: list[dict],
    schema: dict,
    system: str,
    effort: str,
    max_tokens: int,
) -> dict:
    """One structured-output call. Raises on anything that isn't clean JSON.

    Callers catch — every agent's contract is that a failure returns the input
    unchanged, so the exception never escapes this module.
    """
    client = _client()
    response = await client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        system=system,
        output_config={
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined to answer")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("reply was cut off before it was complete")
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise RuntimeError("empty reply")
    return json.loads(text)


# ---------------------------------------------------------------------------
# Agent 1 — relevance
# ---------------------------------------------------------------------------

# The verdict definitions are the whole prompt. They are written as tests a
# human could apply consistently, because a vague instruction ("is it
# relevant?") is what produced the long tail in the first place.
_RELEVANCE_SYSTEM = """You screen e-commerce search results.

The user searched a marketplace for a product. The site answered with whatever \
its keyword matcher returned, which routinely includes parts, accessories and \
unrelated stock. Your job is to say which rows are the product itself.

Classify every row with exactly one verdict:

  match      The row IS the searched product. A different brand, colour, size, \
capacity, material or a multi-pack of it still counts.
  variant    The row is the searched product bundled with something else, or \
sold as a set that is mostly the searched product.
  accessory  The row is a part, refill, add-on, replacement component, cover, \
case, cleaner or holder FOR the product — not the product. A lid for a tumbler \
is an accessory. A stand for a monitor is an accessory.
  unrelated  Anything else, including a different product category that merely \
shares a word with the query, gift cards, services, and listings whose title \
does not identify a product at all.

Rules:
  * Judge only from the title given. Never invent facts about a row.
  * The searched product is the noun the user typed, not the adjectives. \
"stainless steel water bottle" is a water bottle; a stainless steel pan is \
unrelated.
  * When a title is genuinely ambiguous about which of the two it is, prefer \
match. A wrongly hidden real product is a worse error than one extra row.
  * Return a verdict for every index you were given, and no others."""

_RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "verdict": {
                        "type": "string",
                        "enum": ["match", "variant", "accessory", "unrelated"],
                    },
                },
                "required": ["i", "verdict"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


@dataclass
class RelevanceOutcome:
    kept: list[Product]
    dropped: list[Product] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    judged: bool = False


async def _relevance_batch(query: str, batch: list[tuple[int, Product]]) -> dict[int, str]:
    listing = "\n".join(f"{i}. {p.title.strip()[:200]}" for i, p in batch)
    content = [
        {
            "type": "text",
            "text": f"Search query: {query!r}\n\nRows:\n{listing}",
        }
    ]
    data = await _ask(
        content,
        _RELEVANCE_SCHEMA,
        _RELEVANCE_SYSTEM,
        effort="low",
        max_tokens=8000,
    )
    return {
        int(v["i"]): v["verdict"]
        for v in data.get("verdicts", [])
        if isinstance(v, dict) and "i" in v and "verdict" in v
    }


async def filter_by_relevance(query: str, products: list[Product]) -> RelevanceOutcome:
    """Drop the rows that aren't the product the user searched for.

    A row with no verdict — a dropped batch, a model that skipped an index — is
    kept. Every uncertainty in this function resolves towards showing the row,
    because the alternative is deleting a real result with no trace.
    """
    if not products:
        return RelevanceOutcome(kept=[])
    if not is_configured():
        return RelevanceOutcome(kept=products)

    indexed = list(enumerate(products))
    batches = [
        indexed[i : i + RELEVANCE_BATCH] for i in range(0, len(indexed), RELEVANCE_BATCH)
    ]
    results = await asyncio.gather(
        *(_relevance_batch(query, b) for b in batches), return_exceptions=True
    )

    verdicts: dict[int, str] = {}
    failures = 0
    reason = ""
    for outcome in results:
        if isinstance(outcome, BaseException):
            failures += 1
            reason = reason or str(outcome)
            continue
        verdicts.update(outcome)

    warnings: list[str] = []
    if failures:
        warnings.append(
            f"Relevance screening failed for {failures} of {len(batches)} batch(es) "
            f"({reason}) — those listings are shown unscreened."
        )

    kept: list[Product] = []
    dropped: list[Product] = []
    for i, product in indexed:
        verdict = verdicts.get(i)
        product.relevance = verdict
        if verdict in DROP_VERDICTS:
            dropped.append(product)
        else:
            kept.append(product)

    if dropped:
        accessories = sum(1 for p in dropped if p.relevance == "accessory")
        unrelated = len(dropped) - accessories
        parts = []
        if unrelated:
            parts.append(f"{unrelated} unrelated to {query!r}")
        if accessories:
            parts.append(f"{accessories} accessories/parts rather than the product")
        warnings.append(
            f"Hid {len(dropped)} listing(s) the sites returned but that aren't what you "
            f"searched for ({', '.join(parts)}). The remaining {len(kept)} are the real "
            "matches — the result count is not padded back up."
        )

    return RelevanceOutcome(
        kept=kept, dropped=dropped, warnings=warnings, judged=bool(verdicts)
    )


# ---------------------------------------------------------------------------
# Agent 2 — visual supplier matching
# ---------------------------------------------------------------------------

_VISION_SYSTEM = """You match a product photo against supplier catalogue photos.

The first image is the product the buyer wants to source. Each following image \
is one listing from a Chinese B2B marketplace (Alibaba, 1688, Made-in-China, \
AliExpress), labelled with its index and title.

For each candidate, decide what it is relative to the reference product:

  same_product        The same product. Expect a different photographer, \
background, angle, lighting and props — a factory catalogue shot of the same \
item looks nothing like a retail studio shot of it. Judge the object, not the \
photo.
  same_model_variant  The same product in another colour, size or capacity, or \
sold as a multi-pack of it.
  same_category       A different product of the same type — a competing model \
a buyer might still consider.
  different           Not the same kind of product at all.

Rules:
  * Judge the object's form, proportions, construction and distinguishing \
features. Ignore watermarks, collages, price overlays and backgrounds.
  * A supplier photo that is a collage or a lineup counts as same_product if \
the reference product is clearly one of the items shown.
  * confidence is your own certainty from 0 to 1. Use it honestly; a hedged \
same_product at 0.4 is more useful than a false different.
  * note is at most 12 words, naming the feature that decided it.
  * Return one entry per index you were given, and no others."""

_VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "verdict": {
                        "type": "string",
                        "enum": [
                            "same_product",
                            "same_model_variant",
                            "same_category",
                            "different",
                        ],
                    },
                    "confidence": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["i", "verdict", "confidence", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


@dataclass
class VisionVerdict:
    verdict: str
    confidence: float
    note: str

    @property
    def tier(self) -> str | None:
        return VISION_TIER.get(self.verdict)


def _encode_image(image_bytes: bytes) -> dict | None:
    """Downscale to a thumbnail and inline it as JPEG.

    Full-resolution catalogue images are several hundred KB each and buy no
    accuracy for a "is this the same object" judgement, so every image is capped
    at VISION_THUMB_PX on its long edge before it goes in the request.
    """
    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            img.thumbnail((VISION_THUMB_PX, VISION_THUMB_PX))
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=82)
            data = buffer.getvalue()
    except Exception:
        return None
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


async def _fetch_thumb(
    client: httpx.AsyncClient, semaphore: asyncio.Semaphore, url: str
) -> bytes | None:
    async with semaphore:
        try:
            response = await client.get(
                url,
                timeout=IMAGE_FETCH_TIMEOUT_SECONDS,
                follow_redirects=True,
                headers=FETCH_HEADERS,
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPError:
            return None


async def _vision_batch(
    query_block: dict, batch: list[tuple[int, Product, dict]]
) -> dict[int, VisionVerdict]:
    content: list[dict] = [
        {
            "type": "text",
            "text": "Reference product the buyer wants to source:",
        },
        query_block,
    ]
    for index, product, image_block in batch:
        content.append(
            {"type": "text", "text": f"Candidate {index} — {product.title.strip()[:160]}"}
        )
        content.append(image_block)

    data = await _ask(
        content,
        _VISION_SCHEMA,
        _VISION_SYSTEM,
        effort="medium",
        max_tokens=8000,
    )
    verdicts: dict[int, VisionVerdict] = {}
    for entry in data.get("verdicts", []):
        if not isinstance(entry, dict):
            continue
        try:
            verdicts[int(entry["i"])] = VisionVerdict(
                verdict=str(entry["verdict"]),
                confidence=float(entry.get("confidence", 0.0)),
                note=str(entry.get("note", ""))[:120],
            )
        except (KeyError, TypeError, ValueError):
            continue
    return verdicts


async def verify_supplier_matches(
    query_image_bytes: bytes, products: list[Product]
) -> tuple[dict[int, VisionVerdict], list[str]]:
    """Judge supplier listings against the query photo by index into `products`.

    Returns verdicts only for what was actually judged. An index missing from
    the result was never seen — a thumbnail that wouldn't load, a row past
    VISION_TOP_N, or a failed batch — and the caller must keep it on its
    existing evidence rather than treating the silence as a rejection.
    """
    if not products or not is_configured():
        return {}, []

    query_block = _encode_image(query_image_bytes)
    if query_block is None:
        return {}, ["Could not read the query photo for visual matching."]

    candidates = list(enumerate(products))[:VISION_TOP_N]
    semaphore = asyncio.Semaphore(IMAGE_FETCH_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        raw = await asyncio.gather(
            *(
                _fetch_thumb(client, semaphore, p.image_url) if p.image_url else _none()
                for _, p in candidates
            )
        )

    prepared: list[tuple[int, Product, dict]] = []
    unfetchable = 0
    for (index, product), image_bytes in zip(candidates, raw):
        block = _encode_image(image_bytes) if image_bytes else None
        if block is None:
            unfetchable += 1
            continue
        prepared.append((index, product, block))

    warnings: list[str] = []
    if not prepared:
        if unfetchable:
            warnings.append(
                f"None of the {unfetchable} supplier thumbnail(s) could be loaded, so "
                "no listing could be visually checked against your photo."
            )
        return {}, warnings

    batches = [
        prepared[i : i + VISION_BATCH] for i in range(0, len(prepared), VISION_BATCH)
    ]
    results = await asyncio.gather(
        *(_vision_batch(query_block, b) for b in batches), return_exceptions=True
    )

    verdicts: dict[int, VisionVerdict] = {}
    failures = 0
    reason = ""
    for outcome in results:
        if isinstance(outcome, BaseException):
            failures += 1
            reason = reason or str(outcome)
            continue
        verdicts.update(outcome)

    if failures:
        warnings.append(
            f"Visual verification failed for {failures} of {len(batches)} batch(es) "
            f"({reason}) — those listings keep their image-hash tier."
        )
    if unfetchable:
        warnings.append(
            f"{unfetchable} listing(s) had a thumbnail that wouldn't load and could not "
            "be visually checked."
        )
    return verdicts, warnings


async def _none() -> None:
    return None
