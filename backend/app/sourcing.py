"""Photo -> Chinese suppliers, in three stages with a clean tool boundary.

    1. DISCOVER  (Browserbase)  photo -> each site's results URL
    2. EXTRACT   (Zyte)         results URL -> listings
    3. ENRICH    (Zyte)         seller URL -> company profile

The division of labour is the point. A real browser is expensive, slow and
flaky, so it does the one thing only it can do — hand a file to an upload widget
in a way the site accepts as genuine — and then gets out of the way. Everything
after that is a plain URL fetch, which Zyte does far more cheaply, in parallel,
and with retry/ban handling already solved. One browser session per site per
search, then no browser at all.

Extraction prefers the hand-written per-site parsers (already fixture-tested)
and falls back to Zyte's `productList` AI extraction when a parser yields
nothing — which also covers 1688, where writing a parser against an unverified
DOM would be worse than letting Zyte adapt.

Results are tiered rather than filtered, because "same product" and "same
category" are different answers and collapsing them hides the difference:

    identical   phash distance <= 6   the same photo file — near-certainty
    exact       distance <= 12        same product, different shot
    similar     distance <= 20        same category
    unverified  beyond, or unscorable the site returned it; we can't vouch for it

Tiering has two sources, and which one spoke is reported on every row as
`match_basis`:

    phash    perceptual hash distance, as above. Reliable for spotting a reused
             image *file*, weak at "same product, different shoot" — which is
             the normal case here. Measured on a live run, a retail studio photo
             (Amazon) against factory catalogue photos (Made-in-China) put
             *every* real match past distance 20. Filtering on that produced an
             empty grid while the supplier listings underneath were perfectly
             usable, so a phash-unverified row is kept, ranked last, and
             labelled — never silently discarded.
    vision    a Claude verdict on the two photographs (app/claude_agent.py).
             This is the judge the phash measurement above was missing: it
             compares the objects rather than the pixels, so a re-shot product
             reads as the same product. Where it has spoken it sets the tier,
             and a listing it calls `different` IS dropped — that is a real
             answer to "is this the product", not a failure to measure one, and
             showing it anyway would be the padding this app refuses to do.

Rows the vision agent never saw — past its cap, thumbnail wouldn't load, agent
unavailable — keep their phash tier and their `phash` basis. Absence of a
verdict is never read as a rejection.

A wrong "exact" is still a worse failure than an extra row, so the confident
phash tiers keep their tight thresholds.
"""
import asyncio
import base64
from dataclasses import dataclass

import httpx

from . import browserbase_client as bb
from . import claude_agent, supplier_resolve
from .config import settings
from .image_match import HASH_BITS, score_against_query
from .models import Product, SourcingResponse, SourcingResult, SupplierProfile
from .parsing.alibaba_parser import parse_search_results as parse_alibaba
from .parsing.aliexpress_parser import parse_search_results as parse_aliexpress
from .parsing.made_in_china_parser import parse_search_results as parse_mic
from .scrapers import image_discovery
from . import supplier_profile
from .supplier_profile import enrich as enrich_suppliers
from .zyte_client import ZyteClient, ZyteError

SUPPLIER_SITES = ["alibaba", "1688", "made_in_china", "aliexpress"]
DEFAULT_SITES = ["alibaba", "1688", "made_in_china"]

# Browserbase plans cap concurrent browsers (3 on Free, 25 on Developer). Going
# over doesn't queue — the session create fails outright — so cap below the tier.
DISCOVERY_CONCURRENCY = 3

# Enriching every listing's company page would multiply Zyte calls by ~40 per
# search for rows the user will never scroll to. Enrich the best ones only.
ENRICH_TOP_N = 12

TIER_THRESHOLDS = [("identical", 6), ("exact", 12), ("similar", 20)]
# Everything past the last threshold is kept as "unverified" rather than
# dropped — see the module docstring for the live run that forced this.
UNVERIFIED_TIER = "unverified"
TIER_ORDER = {"identical": 0, "exact": 1, "similar": 2, UNVERIFIED_TIER: 3}
CONFIDENT_TIERS = {"identical", "exact"}

# Cheap-first, like MadeInChinaScraper.search_by_text: only sites with real
# anti-bot need the rendered-browser mode.
BROWSER_HTML_SITES = {"alibaba", "aliexpress"}

PARSERS = {
    "alibaba": parse_alibaba,
    "aliexpress": parse_aliexpress,
    "made_in_china": parse_mic,
    # "1688" intentionally absent -> Zyte productList handles it.
}

SITE_LABELS = {
    "alibaba": "Alibaba",
    "1688": "1688",
    "made_in_china": "Made-in-China",
    "aliexpress": "AliExpress",
}


@dataclass
class SiteOutcome:
    site: str
    products: list[Product]
    status: str
    warnings: list[str]


def _tier_for(similarity: float | None) -> str:
    """Never returns None: an unscorable or distant listing is still a listing,
    it just can't be vouched for."""
    if similarity is None:
        return UNVERIFIED_TIER
    distance = (1 - similarity) * HASH_BITS
    for tier, max_distance in TIER_THRESHOLDS:
        if distance <= max_distance:
            return tier
    return UNVERIFIED_TIER


def _decode(result: dict) -> str:
    if result.get("browserHtml"):
        return result["browserHtml"]
    body_b64 = result.get("httpResponseBody", "")
    if not body_b64:
        return ""
    return base64.b64decode(body_b64).decode("utf-8", errors="replace")


def _from_product_list(items: list[dict], site: str) -> list[Product]:
    """Map Zyte's automatic productList output onto our Product model.

    Only maps what productList reliably returns; MOQ and seller identity aren't
    among them, so they stay None rather than being invented. That's the
    known trade for not maintaining a parser."""
    products: list[Product] = []
    for item in items:
        name = (item.get("name") or "").strip()
        url = item.get("url") or ""
        if not name or not url:
            continue

        price = item.get("price")
        try:
            price_min = float(price) if price is not None else None
        except (TypeError, ValueError):
            price_min = None

        main_image = item.get("mainImage")
        image_url = main_image.get("url") if isinstance(main_image, dict) else item.get("mainImageUrl")

        products.append(
            Product(
                site=site,
                title=name,
                product_url=url,
                image_url=image_url,
                price_text=str(price) if price is not None else None,
                price_min=price_min,
                currency=item.get("currency"),
            )
        )
    return products


async def _extract(site: str, discovery: image_discovery.Discovery, zyte: ZyteClient) -> tuple[list[Product], list[str]]:
    """Stage 2. Turn a discovered results page into listings.

    Site parser first (tested, richer — MOQ and seller name), Zyte productList
    second (adapts when the DOM moves, and is the only path for 1688).
    """
    warnings: list[str] = []
    parser = PARSERS.get(site)
    label = SITE_LABELS.get(site, site)

    html = discovery.inline_html
    if html is None and discovery.results_url:
        try:
            # More retries than the default: a website-ban on the results page
            # costs us the *real* parser and silently demotes this site to the
            # generic fallback, so it's worth several extra attempts here.
            result = await zyte.extract(
                discovery.results_url,
                browser_html=site in BROWSER_HTML_SITES,
                http_response_body=site not in BROWSER_HTML_SITES,
                max_retries=4,
            )
            html = _decode(result)
        except ZyteError as e:
            warnings.append(f"[{label}] Zyte could not fetch the results page: {e}")
            html = None

    if html and parser is not None:
        try:
            products = parser(html)
        except Exception as e:  # noqa: BLE001 - a parser bug shouldn't sink the site
            products = []
            warnings.append(f"[{label}] parser error: {e}")
        if products:
            for p in products:
                p.site = site
            return products, warnings
        warnings.append(f"[{label}] site parser found nothing — falling back to Zyte extraction")

    if discovery.results_url:
        try:
            items = await zyte.extract_product_list(discovery.results_url)
        except ZyteError as e:
            warnings.append(f"[{label}] Zyte productList failed: {e}")
            return [], warnings
        products = _from_product_list(items, site)
        if products:
            return products, warnings

    warnings.append(f"[{label}] results page held no parseable listings")
    return [], warnings


async def _run_site(site: str, image_bytes: bytes, content_type: str, zyte: ZyteClient) -> SiteOutcome:
    """Stages 1+2 for one site, chained so extraction starts the moment that
    site's browser session is done rather than waiting on the slowest site."""
    discovery = await image_discovery.discover(site, image_bytes, content_type)
    if not discovery.ok:
        return SiteOutcome(site=site, products=[], status="upload failed", warnings=discovery.warnings)

    products, extract_warnings = await _extract(site, discovery, zyte)
    warnings = discovery.warnings + extract_warnings
    status = f"{len(products)} listing(s)" if products else "no listings"
    return SiteOutcome(site=site, products=products, status=status, warnings=warnings)


def _rank_key(result: SourcingResult) -> tuple:
    """Rank by match confidence first, then by cheapest verified unit price.

    Tier before price on purpose: the cheapest listing is worthless if it isn't
    the same product, and price alone is the classic way sourcing tools surface
    a confident-looking wrong answer.

    Within one tier, a row a vision model actually looked at outranks one that
    only shares a hash bucket — the two are not equally good evidence, and the
    ordering should say so before the price does.
    """
    tier_rank = TIER_ORDER.get(result.match_tier or UNVERIFIED_TIER, 3)
    verified_first = 0 if result.match_basis == "vision" else 1
    confidence = result.match_confidence or 0.0
    price = result.product.price_min if result.product.price_min is not None else float("inf")
    return (tier_rank, verified_first, -confidence, -(result.image_score or 0), price)


def _vision_candidates(results: list[SourcingResult]) -> list[SourcingResult]:
    """Pick which listings are worth a vision call, dealt round-robin by site.

    Every image judged is a downloaded, re-encoded photo in a request body, so
    the set has to be capped. Taking the first N of the phash ordering would
    spend the whole budget on whichever site happened to sort first — and phash
    order is precisely the signal this agent exists to correct, so it is a poor
    guide to what deserves a look. Dealing a slot to each site in turn keeps
    every site the user selected represented, the same fairness rule
    bestsellers._fair_truncate applies to the TOP_N budget.
    """
    by_site: dict[str, list[SourcingResult]] = {}
    for r in results:
        by_site.setdefault(r.product.site, []).append(r)

    picked: list[SourcingResult] = []
    round_index = 0
    while len(picked) < claude_agent.VISION_TOP_N:
        added = False
        for site in sorted(by_site, key=lambda s: SUPPLIER_SITES.index(s) if s in SUPPLIER_SITES else 99):
            bucket = by_site[site]
            if round_index < len(bucket) and len(picked) < claude_agent.VISION_TOP_N:
                picked.append(bucket[round_index])
                added = True
        if not added:
            break
        round_index += 1
    return picked


async def _apply_vision_verdicts(
    results: list[SourcingResult], image_bytes: bytes
) -> tuple[list[SourcingResult], list[str]]:
    """Re-tier listings on what a vision model sees, and drop the ones it says
    are a different product.

    Returns the surviving results and any warnings. A listing with no verdict is
    returned untouched on its phash tier — silence from the agent means "not
    looked at", which must never read as "rejected".
    """
    if not results:
        return results, []

    candidates = _vision_candidates(results)
    verdicts, warnings = await claude_agent.verify_supplier_matches(
        image_bytes, [c.product for c in candidates]
    )
    if not verdicts:
        return results, warnings

    rejected: list[SourcingResult] = []
    for index, verdict in verdicts.items():
        if index >= len(candidates):
            continue
        result = candidates[index]
        result.match_basis = "vision"
        result.match_note = verdict.note or None
        result.match_confidence = round(verdict.confidence, 2)
        tier = verdict.tier
        if tier is None:
            rejected.append(result)
            continue
        # A phash `identical` means the supplier reused the exact photo file,
        # which is stronger evidence than any visual judgement — keep it.
        if result.match_tier != "identical":
            result.match_tier = tier

    if rejected:
        dropped = {id(r) for r in rejected}
        results = [r for r in results if id(r) not in dropped]
        warnings.append(
            f"Hid {len(rejected)} supplier listing(s) whose photo is a different product "
            f"from yours. {len(results)} listing(s) remain — the count is not padded back "
            "up with weaker matches."
        )

    confirmed = sum(1 for r in results if r.match_basis == "vision" and r.match_tier in CONFIDENT_TIERS)
    if confirmed:
        warnings.append(
            f"{confirmed} listing(s) were confirmed as the same product by comparing the "
            "photos directly, not just by image hash."
        )
    return results, warnings


async def source_by_image_url(
    image_url: str,
    sites: list[str] | None = None,
    zyte: ZyteClient | None = None,
    enrich: bool = True,
) -> SourcingResponse:
    """Source from a retail listing's photo URL rather than an upload.

    This is the join between the two halves of the app: Best Seller Search finds
    what's selling, and its product images become the query here. Supplier-site
    CDNs and retail CDNs both 403 a default httpx UA, so the same browser-ish
    headers image_match already uses are reused.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(image_url, headers=headers)
            response.raise_for_status()
    except Exception as e:  # noqa: BLE001 - a dead thumbnail is a user-visible warning, not a 500
        return SourcingResponse(
            results=[],
            warnings=[f"Could not fetch the product photo to search with: {e}"],
        )

    content_type = (response.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
    return await source_by_image(
        response.content, content_type, sites=sites, zyte=zyte, enrich=enrich
    )


async def source_by_image(
    image_bytes: bytes,
    content_type: str,
    sites: list[str] | None = None,
    zyte: ZyteClient | None = None,
    enrich: bool = True,
) -> SourcingResponse:
    """Run the full photo -> suppliers pipeline across the selected sites.

    Every stage degrades independently: a site that gets challenged contributes
    a warning and no rows; a supplier page that won't load leaves that row
    un-enriched. A partial answer is reported as partial — never as an empty
    grid, per the honest-failure rule the scrapers already follow.
    """
    zyte = zyte or ZyteClient()
    site_ids = [s for s in (sites or DEFAULT_SITES) if s in SUPPLIER_SITES]
    if not site_ids:
        return SourcingResponse(results=[], warnings=["No valid supplier sites selected."])

    warnings: list[str] = []
    site_status: dict[str, str] = {}

    if not bb.is_configured():
        return SourcingResponse(
            results=[],
            warnings=[
                "Image sourcing needs Browserbase — set BROWSERBASE_API_KEY and "
                "BROWSERBASE_PROJECT_ID in backend/.env."
            ],
        )

    # --- stages 1 + 2 ---------------------------------------------------
    semaphore = asyncio.Semaphore(DISCOVERY_CONCURRENCY)

    async def _guarded(site: str) -> SiteOutcome:
        async with semaphore:
            return await _run_site(site, image_bytes, content_type, zyte)

    outcomes = await asyncio.gather(*(_guarded(s) for s in site_ids), return_exceptions=True)

    products: list[Product] = []
    for site, outcome in zip(site_ids, outcomes):
        if isinstance(outcome, Exception):
            site_status[site] = "error"
            warnings.append(f"[{SITE_LABELS.get(site, site)}] {outcome}")
            continue
        site_status[site] = outcome.status
        warnings.extend(outcome.warnings)
        products.extend(outcome.products)

    if not products:
        warnings.append("No supplier listings were found for this photo on the selected sites.")
        return SourcingResponse(results=[], warnings=warnings, site_status=site_status)

    # --- visual tiering -------------------------------------------------
    scored = await score_against_query(products, image_bytes)
    results: list[SourcingResult] = []
    for product, similarity in scored:
        product.image_match = round(similarity, 3) if similarity is not None else None
        results.append(
            SourcingResult(
                product=product,
                image_score=round(similarity, 3) if similarity is not None else None,
                match_tier=_tier_for(similarity),
                match_basis="phash",
            )
        )
    results.sort(key=_rank_key)

    # --- vision verdicts ------------------------------------------------
    # The judge the phash measurement in this module's docstring was missing:
    # it reads the products in the two photographs instead of their pixels, so
    # a factory re-shoot of the buyer's product stops reading as "unverified".
    # Where it speaks it sets the tier and drops outright non-matches; where it
    # doesn't, every row keeps exactly the phash tier it already had.
    if settings.claude_visual_matching and claude_agent.is_configured():
        results, vision_warnings = await _apply_vision_verdicts(results, image_bytes)
        warnings.extend(vision_warnings)
        results.sort(key=_rank_key)

    if not results:
        warnings.append(
            "Every supplier listing found was a different product from your photo."
        )
        return SourcingResponse(results=[], warnings=warnings, site_status=site_status)

    confident = sum(1 for r in results if r.match_tier in CONFIDENT_TIERS)
    unverified = sum(1 for r in results if r.match_tier == UNVERIFIED_TIER)
    if not confident and results:
        warnings.append(
            f"No listing matched your photo closely enough to confirm it's the same product. "
            f"Showing all {len(results)} supplier listing(s) found, weakest last — verify before "
            "contacting. (Retail studio photos and factory catalogue photos rarely hash alike, "
            "so this is common and doesn't mean the listings are wrong.)"
        )
    elif unverified:
        warnings.append(f"{unverified} listing(s) could not be visually verified — ranked last.")

    # --- stage 2.5 ------------------------------------------------------
    # Find out who sells these. A search-results card names the product, not
    # the company — so without this, seller_url is None on every row and stage
    # 3 below has nothing to enrich. Runs after visual matching so the page
    # fetches are spent on listings already confirmed to be the buyer's
    # product, and only on rows still missing a seller.
    page_profiles: dict[str, SupplierProfile] = {}
    if settings.resolve_suppliers:
        resolve_warnings, page_profiles = await supplier_resolve.resolve(
            [r.product for r in results], zyte
        )
        warnings.extend(resolve_warnings)

    # --- stage 3 --------------------------------------------------------
    if enrich:
        targets = [
            (r.product.seller_url, r.product.site)
            for r in results[:ENRICH_TOP_N]
            if r.product.seller_url
        ]
        if targets:
            profiles: dict[str, SupplierProfile] = await enrich_suppliers(targets, zyte)
            for r in results[:ENRICH_TOP_N]:
                if r.product.seller_url:
                    r.supplier = profiles.get(r.product.seller_url)
        else:
            warnings.append(
                "No supplier company pages were linked from these listings — "
                "company details unavailable."
            )

    # Combine the two sources rather than choosing between them, because each
    # knows something the other doesn't. The product page carries the
    # authoritative identity — company name, business type, years, the named
    # contact — while only a scan of the company's own site can turn up a
    # published email or phone. Picking the scan alone regressed every row's
    # company to "Alibaba.com" (the minisite's generic <title>); picking the
    # product page alone would throw away contacts wherever a supplier does
    # publish them.
    #
    # Identity fields resolve to the product-page record because it is the
    # stronger source; contact lists are unioned. See supplier_profile._merge.
    for r in results[:ENRICH_TOP_N]:
        page_profile = page_profiles.get(r.product.product_url)
        if not page_profile:
            continue
        scanned = r.supplier
        if scanned:
            page_profile.pages_scanned = scanned.pages_scanned
            supplier_profile.merge_profiles(page_profile, scanned)
            # Keep the scan's verdict on contacts — it is the only thing that
            # actually looked for them.
            if not (page_profile.emails or page_profile.phones or page_profile.whatsapp):
                page_profile.warning = scanned.warning
            else:
                page_profile.warning = None
        r.supplier = page_profile

    return SourcingResponse(results=results, warnings=warnings, site_status=site_status)
