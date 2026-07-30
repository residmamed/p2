"""Photo -> Chinese supplier listings in two REST calls. No browser anywhere.

The second route to the question `app/sourcing.py` answers, and the fast one.
Where that pipeline drives each site's own photo-upload widget in a cloud
browser — minutes, and bot-checked on Alibaba essentially every time — this one
never touches the sites' search at all:

    STEP 1  SerpApi Google Lens   photo -> product URLs across the whole web
    STEP 2  Oxylabs Web Scraper   each Alibaba/1688/Made-in-China URL ->
                                  supplier, price, MOQ

Both are plain REST. Step 1 is one round trip that answers in ~1-2s; step 2 is
N independent fetches fired concurrently. The target is the whole thing under
5 seconds, which is only achievable because nothing here renders a page or waits
on a queue.

**What this trades away.** Lens finds pages that host a matching *image*. It has
no idea what a minimum order quantity is and no special relationship with
Alibaba, so its coverage of Chinese B2B listings is partial and varies by
category — a mass-market houseware will hit, a niche industrial part often
won't. `sourcing.py` searches each site's own reverse-image index and will find
things this cannot. So: not a replacement, a faster first look. And when Lens
returns no marketplace hits at all, that is reported as exactly that, with the
retail matches it did find handed back as `partial_matches` rather than a bare
empty list.

**Provenance, not confidence.** Every row leaves here labelled
`lens_visual_match` or `lens_exact_match` and nothing stronger. No hash distance
is computed and no vision model looks at the two photographs, which is precisely
what `sourcing.py`'s match_tier / match_basis pair exists to record. A Lens hit
is evidence that one image resembles another; presenting it as a confirmed
product match would be the confident-looking wrong answer this codebase keeps
refusing to produce.

**Degradation.** Every step below can fail on its own and the request still
answers. Lens times out -> a clear error, no results. Oxylabs is unconfigured or
rejects the credentials -> every row survives on SerpApi's inline title, price
and thumbnail, each flagged `enriched: false` with the reason. One product page
hangs past its 8s -> that row alone falls back. The one thing this must never do
is return `results: []` without saying why.
"""
import asyncio
import base64
import binascii
import logging
import time
from dataclasses import asdict, dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse

import httpx

from . import lens_cache, serp_lens, supplier_contacts
from .config import settings
from .models import (
    FindSuppliersResponse,
    PartialMatch,
    PriceRange,
    StepTimings,
    SupplierMatch,
)
from .oxylabs_client import (
    PER_URL_TIMEOUT,
    OxylabsAuthError,
    OxylabsClient,
    scrape_many,
    site_for_url,
)
from .parsing.marketplace_product import ParsedProduct, parse_product_page

# The 5s target depends on both steps staying fast and they fail differently —
# Lens degrades all at once, enrichment one page at a time. One line per search
# with both numbers is what makes a regression in either visible without
# instrumenting the caller.
log = logging.getLogger("zyte.lens_suppliers")

# Step 1's whole budget. Both Lens calls run inside it concurrently, so this is
# a wall-clock cap and not a per-call one.
#
# The brief said 5s and that was right for the case it described: one image, one
# step. It is wrong for how this is actually called. The UI searches a basket of
# products at once, one request each, two SerpApi calls per request — and
# SerpApi slows down under that load. Measured 2026-07-29 on the same endpoint:
#
#     1 image  (2 calls)    3.0s, 3.1s
#     5 images (10 calls)   0.1 · 3.2 · 3.2 · 3.9 · 4.6 · 5.9 · 6.1 · 6.2 · 6.3s
#
# So a 5s cap failed most of a five-product search with "Google Lens did not
# answer within 5s" and returned nothing at all, while every one of those calls
# was on its way to a perfectly good 200. The wall clock for all ten was 6.4s —
# concurrency is not the problem, per-call latency under load is. Twenty seconds
# clears the observed spread several times over and still bounds a hung request.
LENS_TIMEOUT = 20.0
# Publishing an upload is step 1's other half. Held separately and kept tight:
# the image host is the slowest, least controlled hop in the pipeline.
UPLOAD_TIMEOUT = 8.0

# Firing every marketplace hit at Oxylabs would spend the latency budget on rows
# nobody scrolls to, and Lens routinely returns 40+. Ordered by Lens match
# position, so the cap takes the best ones.
MAX_ENRICH = 10
ENRICH_CONCURRENCY = 6

# Enrichment targets. Taobao listings are still returned — Lens finds them and
# dropping a real result to keep a schema tidy is not a trade worth making —
# they simply arrive on their Lens data with `enriched: false`.
#
# Made-in-China was added 2026-07-30 on measurement rather than assumption. Over
# the 36 searches then in the Lens cache (12,040 candidates), Lens returned:
#
#     alibaba         89 hits
#     made-in-china   19 hits, 19 of them resolvable
#     taobao           1 hit
#     1688             0 hits
#
# — so it is the second-best-served supplier site in this pipeline, and unusually
# every one of its hits carried a real destination rather than a Lens redirect.
# It also parses well: `parsing/marketplace_product.py`'s generic og:/JSON-LD
# layers already read its title, image, price and — via JSON-LD `brand` — the
# factory's own name, which is the field Alibaba needs a bespoke blob reader for.
ENRICH_SITES = {"alibaba", "1688", "made_in_china"}
MARKETPLACES = ("alibaba", "1688", "taobao", "made_in_china")

# Lens returns Made-in-China category, keyword-search and video pages alongside
# real listings — measured, 6 of 19 were one of those. They are not products:
# enriching one spends an Oxylabs call to read a directory page, and showing it
# as a supplier row puts a link to a search result where a factory should be. So
# a MIC hit has to look like a listing to become a result, and the rest are
# reported as context. Both of the site's listing shapes are covered:
#
#     <company>.en.made-in-china.com/product/<id>/<slug>.html
#     <locale>.made-in-china.com/co_<company>/product_<slug>.html
#
# `/products-search/` deliberately matches neither — it contains the word but not
# the separator, which is the whole reason this tests for `/product/` and
# `/product_` rather than for the substring "product".
MIC_PRODUCT_PATH = ("/product/", "/product_")

MAX_PARTIAL_MATCHES = 25
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Both halves of the Lens answer. `exact_matches` are pages hosting the
# identical image file — the strongest thing Lens can say — and `visual_matches`
# are everything that merely looks like it. Fetched together and kept
# distinguishable, the same split serp_lens.py already relies on.
LENS_TYPES = ("exact_matches", "visual_matches")
CONFIDENCE_FOR_TYPE = {
    "exact_matches": "lens_exact_match",
    "visual_matches": "lens_visual_match",
}

# Leading bytes -> content type, for an uploaded blob that arrives without one.
IMAGE_MAGIC = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF8", "image/gif"),
    (b"BM", "image/bmp"),
]


class FindSuppliersError(Exception):
    """A fault the caller can do something about — bad input, no SerpApi key,
    Lens unreachable. Distinct from the partial failures handled inline."""


@dataclass
class LensCandidate:
    """One Lens hit, before anything has been decided about it."""

    title: str
    product_url: str
    source_domain: str
    marketplace: Optional[str]  # "alibaba" | "1688" | "taobao" | None
    image_url: Optional[str]
    price_text: Optional[str]
    source_name: Optional[str]
    match_confidence: str
    order: int
    # False when `product_url` is one of Google's own redirect wrappers rather
    # than the destination — see LENS_REDIRECT_HOSTS. Such a row cannot be
    # enriched and its real URL is unknown to us, so it never reaches `results`.
    resolvable: bool = True


# --- input handling ---------------------------------------------------------

def _content_type(image_bytes: bytes) -> str:
    for magic, content_type in IMAGE_MAGIC:
        if image_bytes.startswith(magic):
            return content_type
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    # Unrecognised is not the same as invalid — SerpApi only needs the host to
    # serve it back — so guess the common case rather than reject.
    return "image/jpeg"


def decode_image_base64(value: str) -> bytes:
    """Accept both a bare base64 payload and a `data:image/png;base64,...` URL,
    since a browser's FileReader produces the second and a script the first."""
    payload = value.strip()
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
        if not payload:
            raise FindSuppliersError("image_base64 is a data URL with no data after the comma.")
    try:
        image_bytes = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as e:
        raise FindSuppliersError("image_base64 is not valid base64.") from e
    if not image_bytes:
        raise FindSuppliersError("image_base64 decoded to zero bytes.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise FindSuppliersError(
            f"Image too large ({len(image_bytes) // 1024 // 1024}MB) — the limit is 8MB."
        )
    return image_bytes


# --- step 1: Google Lens ----------------------------------------------------

def _domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


# Measured 2026-07-29: every row of a `type=exact_matches` response arrives as
# `https://lens.google.com/goto?url=<token>` rather than the destination. The
# token is not a URL — base64-decoding it yields protobuf-framed ciphertext with
# no plaintext `http` anywhere in it — and fetching the wrapper server-side
# answers 404, browser user-agent included. So the destination of an exact match
# is genuinely unknowable here, and the only way to learn it would be to open
# the redirect in a real browser, which is the one thing this pipeline exists to
# avoid. Those rows are therefore context, never supplier results.
LENS_REDIRECT_HOSTS = {"lens.google.com", "www.google.com", "google.com"}

# SerpApi labels each hit with the site it came from. That label is the only
# marketplace signal left once the URL is a redirect token, and it is good
# enough to *report* a hit — never to link to one. Written as exact labels
# rather than a substring test, because "AliExpress" contains neither of these
# but a loose match on "ali" would swallow it.
MARKETPLACE_BY_SOURCE_LABEL = {
    "alibaba.com": "alibaba",
    "alibaba": "alibaba",
    "1688.com": "1688",
    "1688": "1688",
    "taobao.com": "taobao",
    "taobao": "taobao",
    "taobao global": "taobao",
    "made-in-china.com": "made_in_china",
    "made-in-china": "made_in_china",
    "made in china": "made_in_china",
}


def _is_redirect(url: str) -> bool:
    return _domain_of(url) in LENS_REDIRECT_HOSTS


def _is_product_page(candidate: LensCandidate) -> bool:
    """Does this marketplace hit point at a listing, or at a directory page?

    Only asked of Made-in-China, whose Lens results mix the two. Every other
    site here returns product URLs or nothing, and inventing a shape test for
    them would reject listings on a guess. See MIC_PRODUCT_PATH.
    """
    if candidate.marketplace != "made_in_china":
        return True
    try:
        path = urlparse(candidate.product_url).path.lower()
    except ValueError:
        return False
    return any(marker in path for marker in MIC_PRODUCT_PATH)


def _marketplace_from_source(source_name: Optional[str]) -> Optional[str]:
    if not source_name:
        return None
    return MARKETPLACE_BY_SOURCE_LABEL.get(source_name.strip().lower())


def _canonical(url: str) -> str:
    """A dedupe key for URLs that differ only in tracking noise.

    Lens returns the same Alibaba listing several times over — once per
    `?spm=`, per locale subdomain, per trailing slash — and each duplicate that
    survives costs a real Oxylabs call.
    """
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    # A redirect wrapper is *entirely* query string: every one of them shares
    # the path `/goto`, so dropping the query here would collapse forty distinct
    # matches into one. Keep it, and let them dedupe on the token.
    if host in LENS_REDIRECT_HOSTS:
        return urlunparse(("https", host, parts.path, "", parts.query, "")).lower()
    # `spanish.alibaba.com/product-detail/x` and `www.alibaba.com/product-detail/x`
    # are the same listing in two languages.
    if host.endswith(".alibaba.com"):
        host = "alibaba.com"
    # Made-in-China needs the opposite care taken. One label in front of the
    # domain is a locale (`fr.`, `es.`, `m.`, `www.`) and collapses like
    # Alibaba's; two is a supplier's own minisite
    # (`wisdomhouseware.en.made-in-china.com`), where the subdomain IS the
    # company and flattening it would merge two factories' listings into one.
    elif host.endswith(".made-in-china.com"):
        prefix = host[: -len(".made-in-china.com")]
        if "." not in prefix:
            host = "made-in-china.com"
    path = parts.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", "", "")).lower()


def _candidate_from(match: dict, match_confidence: str, order: int) -> Optional[LensCandidate]:
    link = match.get("link")
    if not isinstance(link, str) or not link.startswith("http"):
        return None
    domain = _domain_of(link)
    if not domain:
        return None

    price = match.get("price")
    if isinstance(price, dict):
        price_text = price.get("value") or price.get("extracted_value")
        price_text = str(price_text) if price_text is not None else None
    elif price is not None:
        price_text = str(price)
    else:
        price_text = None

    source_name = (match.get("source") or "").strip() or None
    resolvable = not _is_redirect(link)
    # A real URL settles the question by itself. Behind a redirect the site
    # label is all that is left, and it is used only to say "there is a hit on
    # Alibaba we cannot reach", never to build a row that claims to link to one.
    marketplace = site_for_url(link) if resolvable else _marketplace_from_source(source_name)

    title = (match.get("title") or "").strip()
    return LensCandidate(
        # A marketplace hit with no title is still a usable lead — the product
        # page will supply one — so unlike serp_lens._to_product this keeps it
        # and names it after its host rather than dropping the row.
        title=title[:300] or f"Untitled listing on {source_name or domain}",
        product_url=link,
        source_domain=domain,
        marketplace=marketplace,
        image_url=match.get("thumbnail") or match.get("image") or None,
        price_text=price_text,
        source_name=source_name,
        match_confidence=match_confidence,
        order=order,
        resolvable=resolvable,
    )


async def _lens_call(
    client: httpx.AsyncClient, image_url: str, search_type: str
) -> tuple[list[LensCandidate], list[str]]:
    try:
        response = await client.get(
            serp_lens.SERPAPI_URL,
            params={
                "engine": "google_lens",
                "type": search_type,
                "url": image_url,
                "api_key": credentials.SERPAPI.next(),
            },
        )
    except httpx.HTTPError as e:
        return [], [f"Lens {search_type} request failed ({type(e).__name__})."]

    if response.status_code != 200:
        return [], [f"Lens {search_type} returned HTTP {response.status_code}."]
    try:
        payload = response.json()
    except ValueError:
        return [], [f"Lens {search_type} returned a non-JSON body."]

    if payload.get("error"):
        message = str(payload["error"])
        # "hasn't returned any results" is an ordinary outcome for a photo
        # nothing on the web hosts, not a fault worth reporting as one.
        if "hasn't returned any results" in message:
            return [], []
        return [], [f"Lens {search_type}: {message[:160]}"]

    # SerpApi has moved this key around between engine versions, and some
    # responses carry shopping rows under their own key. Read them all.
    raw: list = []
    for key in (search_type, "visual_matches", "shopping_results"):
        found = payload.get(key)
        if isinstance(found, list):
            raw.extend(found)

    confidence = CONFIDENCE_FOR_TYPE[search_type]
    candidates: list[LensCandidate] = []
    for index, match in enumerate(raw):
        if isinstance(match, dict):
            candidate = _candidate_from(match, confidence, index)
            if candidate:
                candidates.append(candidate)
    return candidates, []


def _dedupe(candidates: list[LensCandidate]) -> list[LensCandidate]:
    """Keep the first sighting of each listing, ranked by Lens match order.

    Exact matches sort ahead of visual ones at equal position: Lens found the
    identical image file on those pages, which is the stronger claim of the two
    and the only ordering signal this pipeline has.
    """
    ordered = sorted(
        candidates,
        key=lambda c: (0 if c.match_confidence == "lens_exact_match" else 1, c.order),
    )
    seen: set[str] = set()
    kept: list[LensCandidate] = []
    for candidate in ordered:
        key = _canonical(candidate.product_url)
        if key in seen:
            continue
        seen.add(key)
        kept.append(candidate)
    return kept


async def _run_lens(image_url: str) -> tuple[list[LensCandidate], list[str]]:
    """Both Lens searches, concurrently, inside step 1's budget."""
    if not credentials.SERPAPI:
        raise FindSuppliersError(
            "Google Lens is not configured — set SERPAPI_KEY in backend/.env."
        )

    async with httpx.AsyncClient(timeout=LENS_TIMEOUT) as client:
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(_lens_call(client, image_url, t) for t in LENS_TYPES)),
                timeout=LENS_TIMEOUT,
            )
        except asyncio.TimeoutError as e:
            raise FindSuppliersError(
                f"Google Lens did not answer within {LENS_TIMEOUT:.0f}s."
            ) from e

    candidates: list[LensCandidate] = []
    warnings: list[str] = []
    for found, warned in results:
        candidates.extend(found)
        warnings.extend(warned)
    return _dedupe(candidates), warnings


# --- step 2: Oxylabs enrichment ---------------------------------------------

def _price_field(parsed: ParsedProduct, candidate: LensCandidate):
    """A parsed range where one was read, the raw Lens string otherwise.

    The two are different claims and the schema keeps them apart: a PriceRange
    was read off the supplier's own page, a string is whatever text SerpApi
    scraped out of a search result and never checked.
    """
    if parsed.price_min is not None:
        return PriceRange(
            min=parsed.price_min,
            max=parsed.price_max if parsed.price_max is not None else parsed.price_min,
            currency=parsed.currency,
        )
    return parsed.price_text or candidate.price_text


def _from_lens_only(candidate: LensCandidate, reason: Optional[str]) -> SupplierMatch:
    return SupplierMatch(
        # Deliberately None. Lens's `source` names the *host* — it is the string
        # "Alibaba.com" on every Alibaba row — so using it here would print the
        # marketplace where the factory's name goes, on precisely the rows where
        # the factory is unknown. `source` already says which site this is; a
        # blank supplier_name says the one true thing we have, which is nothing.
        supplier_name=None,
        product_title=candidate.title,
        price=candidate.price_text,
        moq=None,
        product_url=candidate.product_url,
        image_url=candidate.image_url,
        source=candidate.marketplace or "",
        match_confidence=candidate.match_confidence,
        enriched=False,
        enrichment_error=reason,
    )


def _merged(candidate: LensCandidate, parsed: ParsedProduct) -> SupplierMatch:
    return SupplierMatch(
        # `source_name` is Lens's label for the *host* ("Alibaba.com"), never the
        # seller, so it is not a fallback for a company name — an absent
        # supplier stays absent rather than being reported as the marketplace.
        supplier_name=parsed.supplier_name,
        # The company's own page, which is what makes the name clickable. Comes
        # from the product page's `companyProfileUrl` and is left absent rather
        # than guessed at when the listing doesn't publish one.
        supplier_url=parsed.supplier_url,
        product_title=parsed.title or candidate.title,
        price=_price_field(parsed, candidate),
        moq=parsed.moq,
        product_url=candidate.product_url,
        image_url=parsed.image_url or candidate.image_url,
        source=candidate.marketplace or "",
        match_confidence=candidate.match_confidence,
        enriched=True,
    )


def _enrich_targets(candidates: list[LensCandidate]) -> list[LensCandidate]:
    """Choose which listings get an Oxylabs call, dealt round-robin by site.

    Taking the first MAX_ENRICH of the Lens ordering spends the whole budget on
    whichever site returned most. Measured 2026-07-30 on a live 40oz-tumbler
    search: 58 marketplace hits, 55 of them Alibaba and 3 Made-in-China, and all
    ten slots went to Alibaba — every Made-in-China row came back unenriched,
    with no supplier name and no MOQ, which is the entire value of the site.

    Volume is not quality here. Lens returns more Alibaba because Google indexes
    more Alibaba, not because those listings match better, so letting it decide
    the budget silently turns a multi-site search back into a single-site one.
    Dealing a slot to each site in turn is the same fairness rule
    sourcing._vision_candidates applies to its vision budget, for the same
    reason. Sites are dealt in order of first appearance, so the one Lens
    matched best still leads.
    """
    by_site: dict[str, list[LensCandidate]] = {}
    for candidate in candidates:
        if candidate.marketplace in ENRICH_SITES:
            by_site.setdefault(candidate.marketplace, []).append(candidate)

    picked: list[LensCandidate] = []
    round_index = 0
    while len(picked) < MAX_ENRICH:
        added = False
        for bucket in by_site.values():
            if round_index < len(bucket) and len(picked) < MAX_ENRICH:
                picked.append(bucket[round_index])
                added = True
        if not added:
            break
        round_index += 1
    return picked


async def _enrich(
    candidates: list[LensCandidate], oxylabs: Optional[OxylabsClient] = None
) -> tuple[list[SupplierMatch], list[str], list[str]]:
    """Open each candidate's product page and read the supplier off it.

    Returns (rows, warnings, errors) with one row per candidate in the order
    given — no candidate is ever dropped here. A page that won't load, won't
    parse, or was never attempted comes back on its Lens data with the reason
    attached, because a supplier listing with a thin record is still a lead and
    silently losing it is the failure mode the brief singles out.
    """
    if not candidates:
        return [], [], []

    oxylabs = oxylabs or OxylabsClient()
    if not oxylabs.is_configured():
        reason = "Oxylabs not configured (OXYLABS_USERNAME / OXYLABS_PASSWORD unset)."
        return (
            [_from_lens_only(c, reason) for c in candidates],
            [
                f"{len(candidates)} marketplace listing(s) were not enriched: {reason} "
                "Titles, prices and thumbnails are SerpApi's own; no MOQ or supplier "
                "name is available without it."
            ],
            [],
        )

    targets = _enrich_targets(candidates)
    target_urls = [c.product_url for c in targets]
    outcomes = await scrape_many(target_urls, oxylabs, concurrency=ENRICH_CONCURRENCY)
    by_url = {url: (html, error) for url, html, error in outcomes}

    rows: list[SupplierMatch] = []
    warnings: list[str] = []
    errors: list[str] = []
    auth_failed = False
    failures = 0
    unparsed = 0

    for candidate in candidates:
        if candidate.product_url not in by_url:
            reason = (
                f"Not enriched: {candidate.marketplace} has no Oxylabs enrichment step."
                if candidate.marketplace not in ENRICH_SITES
                else f"Not enriched: past the {MAX_ENRICH}-page cap for one search."
            )
            rows.append(_from_lens_only(candidate, reason))
            continue

        html, error = by_url[candidate.product_url]
        if error is not None:
            failures += 1
            if isinstance(error, OxylabsAuthError):
                auth_failed = True
            rows.append(_from_lens_only(candidate, str(error)))
            continue

        parsed = parse_product_page(html or "", candidate.marketplace or "")
        if parsed.is_empty():
            unparsed += 1
            rows.append(
                _from_lens_only(
                    candidate,
                    "The product page loaded but published none of the fields we read — "
                    "it may have been a challenge page or a layout we don't parse yet.",
                )
            )
            continue
        rows.append(_merged(candidate, parsed))

    if auth_failed:
        errors.append(
            "Oxylabs rejected the credentials, so no listing could be enriched. The rows "
            "below carry SerpApi's inline data only — no supplier name and no MOQ. Check "
            "OXYLABS_USERNAME / OXYLABS_PASSWORD (the dashboard's API user, not the login)."
        )
    elif failures:
        warnings.append(
            f"{failures} product page(s) could not be read within {PER_URL_TIMEOUT:.0f}s; "
            "those rows fall back to SerpApi's own title, price and thumbnail."
        )
    if unparsed:
        warnings.append(
            f"{unparsed} product page(s) loaded but yielded no supplier fields."
        )
    enriched = sum(1 for r in rows if r.enriched)
    if enriched:
        warnings.append(
            f"{enriched} of {len(rows)} listing(s) were enriched from the supplier's own "
            "product page; the rest show SerpApi data only."
        )
    return rows, warnings, errors


# --- the endpoint's engine --------------------------------------------------

def _serialize(candidates: list[LensCandidate]) -> dict:
    return {"candidates": [asdict(c) for c in candidates]}


def _deserialize(payload: dict) -> list[LensCandidate]:
    rows = payload.get("candidates")
    if not isinstance(rows, list):
        return []
    candidates: list[LensCandidate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            candidates.append(LensCandidate(**row))
        except TypeError:
            # A cache entry written by an older shape of this dataclass. Treated
            # as a miss for that row rather than crashing a live request.
            continue
    return candidates


async def find_suppliers(
    image_url: Optional[str] = None,
    image_base64: Optional[str] = None,
    oxylabs: Optional[OxylabsClient] = None,
    use_cache: bool = True,
    include_contacts: bool = False,
) -> FindSuppliersResponse:
    """The whole pipeline. Raises FindSuppliersError only for faults that make
    an answer impossible — bad input, no SerpApi key, Lens unreachable.
    Everything else degrades into the response."""
    started = time.perf_counter()

    if not image_url and not image_base64:
        raise FindSuppliersError(
            "No image provided — send either image_url or image_base64."
        )
    if image_url and image_base64:
        raise FindSuppliersError(
            "Send either image_url or image_base64, not both."
        )

    warnings: list[str] = []
    errors: list[str] = []
    upload_ms: Optional[int] = None
    image_bytes: Optional[bytes] = None

    # The key is computed from what the caller already gave us, so the cache can
    # be consulted before anything is uploaded or fetched. Ordering this the
    # other way round costs a full image upload on every cache hit — measured at
    # 761ms of a 764ms request, i.e. the entire latency saving handed straight
    # back. An upload is keyed on its bytes; a URL on itself, because fetching
    # the image purely to hash it would put a round trip in front of every
    # request. See lens_cache's docstring.
    if image_base64:
        image_bytes = decode_image_base64(image_base64)
        cache_key = lens_cache.key_for_bytes(image_bytes)
    else:
        cache_key = lens_cache.key_for_url(image_url or "")

    # --- step 1 -----------------------------------------------------------
    lens_started = time.perf_counter()
    cached_payload = lens_cache.get(cache_key) if use_cache else None
    cache_age_days: Optional[int] = None

    if cached_payload is not None:
        candidates = _deserialize(cached_payload)
        age = lens_cache.age_seconds(cache_key)
        cache_age_days = int(age // 86400) if age is not None else None
        was_cached = True
        # An uploaded photo that never had to be published this time round has
        # no live URL to report. The stored one is not offered in its place:
        # the image host expires files within hours, so echoing it back would
        # hand the caller a link that 404s. The content hash is what actually
        # identifies this query.
        public_url = image_url or cache_key
        lens_ms = int((time.perf_counter() - lens_started) * 1000)
    else:
        # --- publish, if we were handed bytes -----------------------------
        # SerpApi's Lens endpoint takes a public URL and nothing else: no
        # upload, no multipart, no base64. serp_lens._publish is the single seam
        # for swapping the anonymous host for an S3/R2 bucket — reused here
        # rather than reimplemented so there is one place to change.
        if image_bytes is not None:
            upload_started = time.perf_counter()
            try:
                public_url = await asyncio.wait_for(
                    serp_lens._publish(image_bytes, _content_type(image_bytes)),
                    timeout=UPLOAD_TIMEOUT,
                )
            except asyncio.TimeoutError as e:
                raise FindSuppliersError(
                    f"Publishing the image for Google Lens took longer than "
                    f"{UPLOAD_TIMEOUT:.0f}s."
                ) from e
            except serp_lens.SerpLensError as e:
                raise FindSuppliersError(str(e)) from e
            upload_ms = int((time.perf_counter() - upload_started) * 1000)
            # The upload is not Lens's latency and shouldn't be charged to it.
            lens_started = time.perf_counter()
        else:
            public_url = image_url or ""

        candidates, lens_warnings = await _run_lens(public_url)
        warnings.extend(lens_warnings)
        was_cached = False
        lens_ms = int((time.perf_counter() - lens_started) * 1000)
        if use_cache and candidates:
            # Only a non-empty answer is worth 30 days. Caching an empty one
            # would freeze a transient Lens miss in place for a month.
            lens_cache.put(cache_key, _serialize(candidates))

    # A marketplace hit only becomes a supplier row if we know where it points.
    # Everything else — retail matches, and marketplace matches stuck behind a
    # Lens redirect — is context.
    marketplace = [c for c in candidates if c.marketplace in MARKETPLACES]
    marketplace_hits = [c for c in marketplace if c.resolvable and _is_product_page(c)]
    unreachable = [c for c in marketplace if not c.resolvable]
    # Reachable, on a supplier site, and still not a listing — a Made-in-China
    # category or keyword-search page. Context, never a supplier row.
    non_product = [c for c in marketplace if c.resolvable and not _is_product_page(c)]
    others = [c for c in candidates if c.marketplace is None]

    if not candidates:
        warnings.append("Google Lens matched this image to nothing on the web.")
    elif not marketplace_hits:
        warnings.append(
            f"Google Lens found {len(candidates)} match(es) but no reachable listing on "
            "Alibaba, 1688, Taobao or Made-in-China — Lens's coverage of Chinese B2B "
            "listings is partial and varies by category. The retail matches it did find are "
            "in partial_matches. For a search of the marketplaces' own image indexes, use "
            "/api/sourcing/by-url."
        )
    if non_product:
        # Said plainly because the alternative is worse than silence: these
        # would otherwise be enriched as if they were listings, and a category
        # page parses into a supplier row with a plausible title and no factory
        # behind it.
        warnings.append(
            f"{len(non_product)} Made-in-China match(es) were category, keyword-search or "
            "video pages rather than product listings, so they were not enriched. They are "
            "in partial_matches with their Lens link."
        )
    if unreachable:
        # Naming the count matters: without it, "no marketplace results" reads
        # as "no supplier sells this", when what actually happened is that Lens
        # found some and handed back a redirect we cannot follow.
        sites = ", ".join(sorted({c.marketplace for c in unreachable if c.marketplace}))
        warnings.append(
            f"Google Lens reported {len(unreachable)} exact image match(es) on {sites} but "
            "returned them behind a redirect whose destination it does not disclose, so they "
            "could not be opened or enriched. They are listed in partial_matches with the "
            "Lens link. /api/sourcing/by-url searches those sites' own image indexes directly."
        )

    # --- step 2 -----------------------------------------------------------
    enrich_started = time.perf_counter()
    results, enrich_warnings, enrich_errors = await _enrich(marketplace_hits, oxylabs)
    enrichment_ms = int((time.perf_counter() - enrich_started) * 1000)
    warnings.extend(enrich_warnings)
    errors.extend(enrich_errors)

    # --- step 3, only when asked -----------------------------------------
    # Reading each supplier's own site for a published email or phone, pictures
    # included. Off unless requested: it is several page fetches and a vision
    # call per supplier, which is a different order of cost from the two steps
    # above and would quietly turn a 5-second endpoint into a 30-second one.
    contacts_ms: Optional[int] = None
    if include_contacts and results:
        contacts_started = time.perf_counter()
        warnings.extend(await supplier_contacts.enrich_matches(results, oxylabs))
        contacts_ms = int((time.perf_counter() - contacts_started) * 1000)

    # Marketplace hits lead: they are the closest thing to a supplier lead in
    # this list, and burying them under retail rows would hide the one fact the
    # warnings above just promised. Unreachable first, then the reachable
    # not-a-listing pages — a Made-in-China category page for the right product
    # is at least a page the user can open and search themselves.
    supplier_context = unreachable + non_product
    context = (supplier_context + others)[:MAX_PARTIAL_MATCHES]
    partial_matches = [
        PartialMatch(
            title=c.title,
            product_url=c.product_url,
            image_url=c.image_url,
            price=c.price_text,
            source_domain=c.source_domain,
            source_name=c.source_name,
            match_confidence=c.match_confidence,
        )
        for c in context
    ]
    dropped = len(supplier_context) + len(others) - len(context)
    if dropped:
        warnings.append(
            f"Showing {MAX_PARTIAL_MATCHES} of {len(supplier_context) + len(others)} "
            "context match(es)."
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "find_suppliers: total=%dms lens=%dms%s enrich=%dms%s | cache=%s candidates=%d "
        "marketplace=%d enriched=%d partial=%d",
        latency_ms,
        lens_ms,
        f" upload={upload_ms}ms" if upload_ms is not None else "",
        enrichment_ms,
        f" contacts={contacts_ms}ms" if contacts_ms is not None else "",
        "hit" if was_cached else "miss",
        len(candidates),
        len(marketplace_hits),
        sum(1 for r in results if r.enriched),
        len(partial_matches),
    )

    return FindSuppliersResponse(
        query_image=public_url,
        results=results,
        partial_matches=partial_matches,
        latency_ms=latency_ms,
        step_timings=StepTimings(
            lens_ms=lens_ms,
            enrichment_ms=enrichment_ms,
            upload_ms=upload_ms,
            contacts_ms=contacts_ms,
        ),
        cached=was_cached,
        cache_age_days=cache_age_days,
        warnings=warnings,
        errors=errors,
    )
