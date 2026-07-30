"""Best Seller Search — see CONTEXT.md.

Given a keyword, fan out to several consumer-retail sites concurrently, pull
each site's product listing through whichever transport actually reaches it
(SerpApi for Amazon and Walmart, an Apify actor or a cloud browser for the
sites Zyte is banned from, Zyte's productList extraction for the rest), score
every listing on a common 0-1 scale (Normalized Score), merge identical
products across sites by Shared Identifier only, and return one combined
top-100 (Combined Rank).

Ranking signal, per the design decisions:
  A. If a site offers a "best selling" sort, use Site Rank (the 1-based
     position in that sorted list) as the signal.
  B. Otherwise fall back to a Popularity Score = rating x review_count.
Each signal is min-max normalized *within that site's own results for the
query*, so an ordinal position and a review-weighted rating become comparable
across sites.
"""

import asyncio
import re
import time
from dataclasses import dataclass, field, replace
from urllib.parse import quote_plus

from . import (
    apify_retail,
    claude_agent,
    google_shopping,
    product_page_enrich,
    rainforest,
    retail_browser,
    serpapi_retail,
)
from .config import settings
from .models import Product, SearchResponse, Seller
from .parsing import walmart_parser
from .product_images import best_image
from .zyte_client import ZyteClient, ZyteError


# How a site's ranking signal was actually obtained, strongest first. Carried
# on every Product so a Combined Rank can never silently compare a real
# best-seller ordering against mere page order — and so the UI can say which
# one it's showing. See CONTEXT.md "Rank Basis".
#
# The confidence weight multiplies a site's Normalized Score before the
# cross-site merge: without it, an IKEA result ordered by nothing but relevance
# would outrank a genuine Amazon best-seller, which is precisely the kind of
# confident-looking wrong answer this app is supposed to avoid.
RANK_BASIS_WEIGHT = {
    # Measured purchase volume outranks a sort position, and the live data is
    # why: Amazon reports "20K+ bought in past month" as a magnitude, whereas
    # Walmart's best_seller sort only says "this one came first" — position 1
    # and position 2 could differ by 100x or by nothing. Ranking the opinion
    # above the measurement put a Walmart row with no demand data ahead of a
    # 20,000-unit-a-month best seller.
    "sold_count": 1.00,       # "N bought in past month" / "N sold" — real demand
    "bestseller_sort": 0.92,  # the site's own best-selling order, magnitude unknown
    "rating": 0.70,           # rating x review_count — history, not current sales
    "relevance": 0.45,        # page order only; honest but weak
}


@dataclass(frozen=True)
class SiteConfig:
    id: str
    label: str
    # {q} is replaced with the URL-encoded query, sort param included.
    search_url: str
    # Best signal this site actually offers — verified live, see below.
    rank_basis: str = "relevance"
    # Zyte can't reach every site. via_apify runs a dedicated actor
    # (app/apify_retail.py); via_browser is the Browserbase fallback
    # (app/retail_browser.py) for sites no actor handles.
    via_apify: bool = False
    via_browser: bool = False
    # Prefer a dedicated structured API where one exists — SerpApi's amazon and
    # walmart engines (app/serpapi_retail.py), each asked for the site's own
    # best-selling sort. Falls back to Rainforest (Amazon) or the Zyte path.
    via_api: bool = False
    # Not a store at all: the keyword is answered by picture, via Pinterest
    # images run through Google Lens (app/google_shopping.py). Has no fallback
    # transport, because no other transport can answer the same question.
    via_google_shopping: bool = False
    # Site ships its full search model as embedded page JSON, which productList
    # doesn't surface. Where set, the Zyte fallback reads products from that
    # blob and uses productList only to price them. See _fetch_walmart_via_zyte.
    products_from_page_json: bool = False
    # Site publishes ratings only on individual product pages (JSON-LD), not in
    # search results — costs one request per product, so only worth it where the
    # result set is small. See product_page_enrich.
    ratings_from_product_page: bool = False
    # Walmart's productList price comes back in cents (e.g. "4996.0" = $49.96),
    # inconsistently between calls, while other sites report dollars. When set,
    # a whole-number price >= 1000 is treated as cents and divided by 100.
    price_cents_guard: bool = False


# Every entry below was probed live rather than assumed — see
# spikes/probe_bestseller_sorts.py and spikes/probe_hard_sites.py, whose output
# is summarised in the README. Two findings shaped this table:
#
#   * Amazon and Walmart both honour a real best-selling sort in a plain URL
#     (the top of the Amazon list becomes Owala/Stanley, which are in fact the
#     best sellers). Target and Home Depot joined them later, through actors
#     whose input schemas expose the stores' own sorts (sort=bestselling and
#     sortBy=top_sellers); those four are the Tier-1 sources.
#   * Zyte returns 0 products for Temu and HTTP 520 "Website Ban" for Costco in
#     every extraction mode, so both are routed through Browserbase.
#
# IKEA is deliberately marked "relevance". Six sort values have now been probed
# live (see spikes/probe_bestseller_sorts.py): BEST_SELLER, POPULARITY and
# TOP_SELLER leave the ordering identical to the default, and MOST_POPULAR,
# RATING and BESTSELLER throw the query away — "mirror" comes back as dressers
# and a remote control. Inventing a Site Rank from an unsorted page, or taking
# an ordering that no longer answers the search, would both be fabricated
# rankings.
#
# Temu is ranked, not sorted: its Apify actor's input schema accepts only
# searchQueries/currency/maxResults, so no top-seller URL param can be passed.
# The actor does return sales_num ("25K+ sold") per product, and this module
# orders Temu's rows by it — which is a stronger claim than a sort position,
# since it's the measured volume rather than the site's opinion of it.
#
# Costco is left unsorted on purpose: whatever its search returns first is what
# is shown, ranked by rating x reviews since it publishes no sales figures.
# Pinterest carries no sort either — it isn't a store and never enters this
# table; it's the inspiration path in app/pinterest.py.
SITES: dict[str, SiteConfig] = {
    # Amazon and Walmart both go through a structured API asked for the site's
    # own best-selling order — Rainforest for Amazon, SerpApi for Walmart, each
    # falling through to the other (see _fetch_via_api). Both return rating,
    # review count and a real identifier, none of which Zyte's productList
    # provides for either site. The URLs below are the last fallback, and carry
    # the same sort.
    "amazon": SiteConfig(
        "amazon", "Amazon",
        "https://www.amazon.com/s?k={q}&s=exact-aware-popularity-rank",
        rank_basis="bestseller_sort",
        via_api=True,
    ),
    "walmart": SiteConfig(
        "walmart", "Walmart",
        "https://www.walmart.com/search?q={q}&sort=best_seller",
        rank_basis="bestseller_sort",
        via_api=True,
        # Both flags below apply to the Zyte fallback only. SerpApi returns a
        # typed offer_price and per-listing ratings, so neither the cents
        # heuristic nor the two-source dance runs on the primary path.
        price_cents_guard=True,
        # __NEXT_DATA__ gives stars, review counts, ids and clean titles but
        # zeroed prices; productList gives prices. See _fetch_walmart_via_zyte.
        products_from_page_json=True,
    ),
    "temu": SiteConfig(
        "temu", "Temu",
        "https://www.temu.com/search_result.html?search_key={q}",
        # sales_num ("25K+ sold") comes straight off the actor's payload, so
        # Temu ranks on units actually sold rather than page order.
        rank_basis="sold_count",
        via_apify=True,
    ),
    "costco": SiteConfig(
        "costco", "Costco",
        "https://www.costco.com/CatalogSearch?keyword={q}",
        # Deliberately unsorted: Costco's own search order is what's shown. It
        # used to be reordered by rating x reviews, which reads as a ranking the
        # site never made — a 5-star item with 8 reviews outranking Costco's own
        # first result. Rating and review count still travel on every row and
        # still feed the Opportunity Score; they just don't reshuffle the grid.
        rank_basis="relevance",
        via_apify=True,
    ),
    "ikea": SiteConfig(
        "ikea", "IKEA",
        "https://www.ikea.com/us/en/search/?q={q}",
        rank_basis="relevance",
        # IKEA's search results carry no ratings, but each product page ships
        # schema.org JSON-LD with aggregateRating. Affordable here precisely
        # because IKEA returns only a handful of results per query.
        ratings_from_product_page=True,
    ),

    # The six below all run through app/apify_retail.py. Each rank_basis was
    # decided by what the actor's input schema actually offers, not by what the
    # store claims to support — see that module's table.
    "target": SiteConfig(
        "target", "Target",
        "https://www.target.com/s?searchTerm={q}&sortBy=bestselling",
        # Target's actor takes sort=bestselling and honours it: the probe put
        # Owala first for "water bottle", which is the genuine best seller.
        rank_basis="bestseller_sort",
        via_apify=True,
    ),
    "homedepot": SiteConfig(
        "homedepot", "Home Depot",
        "https://www.homedepot.com/s/{q}",
        # sortBy=top_sellers, Home Depot's own ordering. This site publishes no
        # rating in search results, so the sort is the only signal it offers —
        # and it's a stronger one than a rating would have been.
        rank_basis="bestseller_sort",
        via_apify=True,
    ),
    "ebay": SiteConfig(
        "ebay", "eBay",
        "https://www.ebay.com/sch/i.html?_nkw={q}",
        # eBay's sort enum has no best-selling option, and the actor's soldCount
        # field came back empty on every probed row. Its ratings are seller
        # feedback across all of a seller's sales, not this product's rating, so
        # there is nothing here but page order — and it says so.
        rank_basis="relevance",
        via_apify=True,
    ),
    "etsy": SiteConfig(
        "etsy", "Etsy",
        "https://www.etsy.com/search?q={q}",
        # Etsy's actor returns a rating with no review count at all, so a
        # rating-based ordering here would rank a 5.0 backed by nothing above a
        # 4.7 backed by thousands. Left on relevance for the same reason Costco
        # is: the rating still travels on the row and still feeds the
        # Opportunity Score, it just doesn't reshuffle the grid.
        rank_basis="relevance",
        via_apify=True,
    ),
    "bestbuy": SiteConfig(
        "bestbuy", "Best Buy",
        "https://www.bestbuy.com/site/searchpage.jsp?st={q}",
        # The actor takes only startUrls — no sort parameter of any kind — so
        # whatever Best Buy's search returns first is what's shown. Ratings and
        # review counts are real and complete here; they just aren't a ranking
        # Best Buy made, so they don't reorder the grid (see Costco).
        rank_basis="relevance",
        via_apify=True,
    ),
    "wayfair": SiteConfig(
        "wayfair", "Wayfair",
        "https://www.wayfair.com/keyword.php?keyword={q}",
        # Same as Best Buy: startUrls only, no sort. Wayfair had the most
        # complete data of the six — price, rating and review count on every
        # probed row.
        rank_basis="relevance",
        via_apify=True,
    ),

    # Not a store. The keyword goes to Pinterest, the images Pinterest returns go
    # to Google Lens, and what comes back is whatever the web is selling that
    # looks like them — then filtered down to the listings whose titles actually
    # describe the keyword. See app/google_shopping.py; the search_url below is
    # only for a human following the row back to something familiar.
    #
    # relevance is not a fallback here, it is the truth: nothing in this chain
    # ranks anything. Lens returns visual similarity, which says nothing about
    # what sells, so these rows carry the lowest confidence weight in the merge
    # and are labelled accordingly rather than being mixed in as best sellers.
    "google_shopping": SiteConfig(
        "google_shopping", "Google Shopping",
        "https://www.google.com/search?tbm=shop&q={q}",
        rank_basis="relevance",
        via_google_shopping=True,
    ),
}
ALL_SITES = list(SITES.keys())

TOP_N = 100
CACHE_TTL_SECONDS = 30 * 60  # Result Cache — best-seller order doesn't move minute-to-minute


# ---------------------------------------------------------------------------
# Extraction of raw fields off Zyte's productList items (schema is defensive:
# any field can be missing on any site).
# ---------------------------------------------------------------------------

def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _price_fields(item: dict, site: SiteConfig) -> tuple[str | None, float | None, str | None]:
    price_min = _to_float(item.get("price"))
    currency = item.get("currencyRaw") or item.get("currency")
    # Zero means "this page didn't publish a price" on every source feeding this
    # — Walmart zeroes __NEXT_DATA__ prices outright, and productList passes the
    # zero straight through. Reporting it as $0.00 is worse than reporting
    # nothing: it survives every has-a-price check downstream and then sinks the
    # Market Snapshot median and the margin calculator.
    if not price_min:
        return None, None, currency
    # Walmart's productList mixes cents and dollars *within one response* —
    # measured on a single search: "6068.0" ($60.68), "1794.0" ($17.94),
    # "997.0" ($9.97) alongside "14.0" ($14.00). The page's own __NEXT_DATA__
    # carries no prices at all (Walmart strips them server-side), so there's no
    # authoritative second source to reconcile against and this stays a
    # heuristic on the magnitude.
    #
    # Whole number >= 100 is read as cents. The old >= 1000 threshold let
    # "997.0" through as a $997 water bottle, which then skewed the Market
    # Snapshot's median and the margin calculator. Trade-off: a genuine
    # whole-dollar item priced at $100 or more is misread — rare in this
    # catalogue, and far less damaging than a 100x overstatement.
    if site.price_cents_guard and price_min >= 100 and price_min == int(price_min):
        price_min = price_min / 100
    sep = "" if (currency and not currency[-1].isalnum()) else " "
    price_text = f"{currency}{sep}{price_min:.2f}" if currency else f"{price_min:.2f}"
    return price_text, price_min, currency


def _rating_fields(item: dict) -> tuple[float | None, int | None]:
    agg = item.get("aggregateRating") or {}
    rating = _to_float(agg.get("ratingValue"))
    review_count = _to_int(agg.get("reviewCount"))
    return rating, review_count


def _identifier(item: dict) -> str | None:
    """A Shared Identifier if the listing exposes one. productList extraction
    rarely includes GTINs, so this is usually None and merging simply doesn't
    fire — the deliberate, conservative choice (never merge two different
    products) from CONTEXT.md 'Shared Identifier'."""
    for key in ("gtin", "mpn", "productId", "sku"):
        val = item.get(key)
        if isinstance(val, list):
            val = next((v for v in val if v), None)
        if isinstance(val, dict):
            val = val.get("value")
        if val:
            return f"{key}:{val}"
    return None


# Ad/redirect hosts and paths that mark a *sponsored* listing rather than an
# organic best-seller. Ranking a paid placement as "#1 best seller" would be
# wrong, so these are dropped before scoring.
_SPONSORED_MARKERS = ("aax-", "/sspa/", "/gp/slredirect", "adclick", "/aclk", "doubleclick")


def _is_sponsored(url: str) -> bool:
    u = url.lower()
    return any(marker in u for marker in _SPONSORED_MARKERS)


# Walmart's productList `name` arrives with the site's own badge chrome glued to
# the front — "Best seller Zak Designs 20oz…", "100+ bought since yesterday TAL
# 40 oz…". Left alone it corrupts the title everywhere downstream (export,
# compare, $/oz title parsing) AND throws away a real demand figure. Both get
# fixed here: strip the prefix, keep the number.
BADGE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    # Demand badges — the number is kept, the words are not.
    r"(?P<bought>[\d,]+)\+?\s*bought\s+since\s+yesterday"
    r"|In\s+(?P<carts>[\d,]+)\+?\s+people'?s?\s+carts?"
    r"|(?P<viewed>[\d,]+)\+?\s*(?:viewed|looked\s+at)\s+since\s+yesterday"
    # Pure chrome — no number worth keeping.
    r"|Best\s?seller|Overall\s+pick|Popular\s+pick|Top\s+pick|Rollback|Clearance"
    r"|Amazon's\s+Choice|#1\s+Best\s+Seller|Sponsored|Featured|Reduced\s+price"
    r"|Save\s+with\s+\w+|Free\s+shipping|Best\s+value"
    r")\s*[-|·,]?\s*",
    re.I,
)


def _strip_badges(name: str) -> tuple[str, int | None]:
    """Peel leading badge phrases off a listing title. Returns the clean title
    and any "N bought since yesterday" figure found, which is a stronger demand
    signal than list position alone."""
    bought: int | None = None
    while True:
        m = BADGE_PREFIX_RE.match(name)
        if not m:
            break
        # "bought since yesterday" is a purchase; carts/views are weaker intent,
        # so they only fill in when no purchase figure was found.
        for group in ("bought", "carts", "viewed"):
            raw = m.group(group)
            if raw and bought is None:
                try:
                    bought = int(raw.replace(",", ""))
                except ValueError:
                    pass
                break
        name = name[m.end():]
    return name.strip(" -|·,"), bought


def _title_key(name: str) -> str:
    """A join key for the same product listed by two different Walmart fetches.

    Badge chrome is stripped (productList prefixes names with it, the page JSON
    doesn't) and everything non-alphanumeric is dropped, so "TAL 26oz Stainless
    Steel Ranger Water Bottle" matches regardless of punctuation or spacing.
    """
    cleaned, _ = _strip_badges(name or "")
    return re.sub(r"[^a-z0-9]", "", cleaned.lower())[:70]


def _to_product(item: dict, site: SiteConfig) -> Product | None:
    name = (item.get("name") or "").strip()
    url = item.get("url")
    if not name or not url or _is_sponsored(url):
        return None
    name, bought_recently = _strip_badges(name)
    if not name:
        return None
    price_text, price_min, currency = _price_fields(item, site)
    rating, review_count = _rating_fields(item)
    image = item.get("mainImage") or {}
    image_url = image.get("url") if isinstance(image, dict) else None

    return Product(
        site=site.id,
        title=name,
        image_url=best_image(image_url, site=site.id),
        price_text=price_text,
        price_min=price_min,
        price_max=price_min,
        currency=currency,
        product_url=url,
        seller_name=site.label,
        rating=rating,
        review_count=review_count,
        # A recent-purchase count off the site's own badge is real demand data;
        # it rides in popularity_score, same as retail_browser's sold counts.
        popularity_score=bought_recently,
        identifier=_identifier(item),
    )


# ---------------------------------------------------------------------------
# Per-site fetch + Normalized Score assignment
# ---------------------------------------------------------------------------

@dataclass
class SiteResult:
    products: list[Product] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def _fetch_site(site: SiteConfig, query: str, zyte: ZyteClient) -> SiteResult:
    """Fetch one site's results, through whichever transport actually reaches it."""
    if site.via_google_shopping:
        # Deliberately no fallback: every other transport here answers a keyword
        # by querying a store, which is the one thing this source doesn't do.
        # Falling through to Zyte would return a different question's answer.
        products, warnings = await google_shopping.search(query)
        if not products:
            return SiteResult(warnings=warnings)
        for pos, p in enumerate(products, start=1):
            p.site_rank = pos
        return SiteResult(products=products, warnings=warnings + _assign_normalized_scores(products, site))

    if site.via_api:
        products, warnings = await _fetch_via_api(site, query)
        if products:
            for pos, p in enumerate(products, start=1):
                p.site_rank = pos
            # These results came back in the site's own best-selling order, so
            # position IS the ranking. Where a source also publishes purchase
            # volume ("20K+ bought in past month" on Amazon), that measurement
            # is the stronger signal and _assign_normalized_scores uses it.
            basis = "sold_count" if any(p.popularity_score for p in products) else site.rank_basis
            api_site = replace(site, rank_basis=basis)
            return SiteResult(
                products=products,
                warnings=warnings + _assign_normalized_scores(products, api_site),
            )
        # Fall through to the Zyte path rather than losing the site entirely.

    if site.via_apify or site.via_browser:
        if site.via_apify:
            products, warnings = await apify_retail.fetch_site(site.id, query)
            if not products and site.id in retail_browser.BROWSER_SITES:
                # Actor blocked today — try the cloud browser before giving up.
                fallback, fb_warnings = await retail_browser.fetch_site(
                    retail_browser.BROWSER_SITES[site.id], query
                )
                products, warnings = fallback, warnings + fb_warnings
        else:
            products, warnings = await retail_browser.fetch_site(
                retail_browser.BROWSER_SITES[site.id], query
            )
        if not products:
            return SiteResult(warnings=warnings or [f"[{site.label}] No products returned."])
        for pos, p in enumerate(products, start=1):
            p.site_rank = pos
        return SiteResult(products=products, warnings=warnings + _assign_normalized_scores(products, site))

    url = site.search_url.format(q=quote_plus(query))

    if site.products_from_page_json:
        return await _fetch_walmart_via_zyte(site, url, zyte)

    try:
        items = await _product_list_with_empty_retry(site, url, zyte)
    except ZyteError as e:
        return SiteResult(warnings=[f"[{site.label}] {e}"])
    except Exception as e:  # noqa: BLE001 - one site must not sink the merge
        return SiteResult(warnings=[f"[{site.label}] Unexpected error: {e}"])

    # Filter sponsored/invalid items first, THEN assign Site Rank so positions
    # are contiguous over organic results only (1, 2, 3, ...).
    products: list[Product] = []
    for item in items:
        p = _to_product(item, site)
        if p:
            p.site_rank = len(products) + 1
            products.append(p)

    if not products:
        return SiteResult(warnings=[f"[{site.label}] {_EMPTY_MESSAGE}"])

    warnings: list[str] = []
    if site.ratings_from_product_page:
        warnings += await product_page_enrich.enrich(products, zyte)

    warnings += _assign_normalized_scores(products, site)
    return SiteResult(products=products, warnings=warnings)


async def _fetch_via_api(site: SiteConfig, query: str) -> tuple[list[Product], list[str]]:
    """Try the structured APIs for a site, best source first.

    Amazon prefers Rainforest and Walmart prefers SerpApi, and both then fall
    through to the other where one exists. Both sources are asked for the site's
    own best-selling sort, so the ordering is equivalent either way; what splits
    them is precision. Rainforest reports exact review counts where SerpApi
    rounds to three significant figures (131,434 -> 131,400), which is why
    Amazon leads with it. SerpApi is the only one of the two that covers
    Walmart at all.

    Returns empty products when every API is unavailable, which sends the caller
    to the Zyte path rather than dropping the site.
    """
    warnings: list[str] = []

    async def via_rainforest() -> list[Product] | None:
        if site.id != "amazon" or not rainforest.is_configured():
            return None
        try:
            products, warned = await rainforest.search(query)
        except rainforest.RainforestError as e:
            warnings.append(f"[{site.label}] Rainforest: {e}")
            return None
        warnings.extend(warned)
        return products or None

    async def via_serpapi() -> list[Product] | None:
        if not serpapi_retail.is_configured() or site.id not in serpapi_retail.SUPPORTED_SITES:
            return None
        try:
            products, warned = await serpapi_retail.search(site.id, query)
        except serpapi_retail.SerpApiError as e:
            warnings.append(f"[{site.label}] SerpApi: {e}")
            return None
        warnings.extend(warned)
        return products or None

    sources = (via_rainforest, via_serpapi) if site.id == "amazon" else (via_serpapi,)
    for source in sources:
        products = await source()
        if products:
            return products, warnings

    return [], warnings


async def _fetch_walmart_via_zyte(
    site: SiteConfig, url: str, zyte: ZyteClient
) -> SiteResult:
    """Walmart's fallback path, when SerpApi is unavailable.

    Walmart splits one search across two representations of the same page, and
    neither is sufficient alone:

        __NEXT_DATA__   ratings, review counts, usItemId, clean titles,
                        products only          -- but every price zeroed
        productList     prices                 -- but no ratings, and it also
                        returns promo tiles as though they were products

    This used to run productList first and join ratings onto it. That join was
    measured at 42%, and the reason is not fixable by better keys: the two
    fetches genuinely come back with *different products*. On one live search,
    17 of 40 productList item ids simply weren't in the other response at all —
    Walmart serves different result sets to different requests. Joining the
    scarce data onto the plentiful side meant most rows lost their stars, and
    the failure surfaced as the honest-but-alarming "ratings matched for only 0
    of 18 listings".

    So the blob is the source of truth now — it decides which products exist,
    in what order, with what ratings — and productList is consulted only to
    price them. A price that fails to join is simply absent, which is a far
    smaller loss than a missing rating was, and Walmart's Rank Basis is its
    best-selling position rather than rating, so ranking is unaffected either
    way.

    Falls back to the productList-only path if the blob can't be read, so a
    Walmart reshuffle costs ratings rather than the site.
    """
    listings, blob = await asyncio.gather(
        _zyte_product_list(site, url, zyte),
        _walmart_next_data(url, zyte),
        return_exceptions=True,
    )
    items = listings if isinstance(listings, list) else []
    products, rendered_html = blob if isinstance(blob, tuple) else ([], "")

    if not products:
        # No blob — fall back to the old shape: priced rows, no stars.
        if isinstance(listings, BaseException):
            return SiteResult(warnings=[f"[{site.label}] {listings}"])
        products = [p for p in (_to_product(i, site) for i in items) if p]
        if not products:
            return SiteResult(warnings=[f"[{site.label}] No products returned."])
        for pos, p in enumerate(products, start=1):
            p.site_rank = pos
        return SiteResult(
            products=products,
            warnings=[
                f"[{site.label}] Ratings unavailable for this search — the page's "
                "embedded data could not be read, so these show no stars."
            ]
            + _assign_normalized_scores(products, site),
        )

    # Price index off productList, keyed by the item id both sides embed in
    # their URLs. Promo tiles ("Up to 35% off cookware") have no /ip/ id and so
    # drop out here for free — they were being shown as products.
    #
    # Titles are indexed as a second key. Measured on a live search the id join
    # reaches 36 of 40 rows, and the remainder are the same products under a URL
    # whose id didn't parse — not, as previously assumed, a different result
    # set. Matching those on their title recovers the price instead of leaving
    # the row blank. Badge chrome is stripped first because productList prefixes
    # names with it ("In 200+ people's carts Mainstays 24 oz…") while the page's
    # own JSON does not, so the raw strings never compare equal.
    prices: dict[str, tuple[str | None, float | None, str | None]] = {}
    by_title: dict[str, tuple[str | None, float | None, str | None]] = {}

    # Prices painted on the page these products actually came from. Measured on
    # one live search: productList priced 21 of the 40 blob rows and the
    # rendered grid priced 22 — but not the same rows, so together they reach
    # 28. Neither source alone is enough, which is why both are indexed.
    for item_id, value in walmart_parser.prices_from_dom(rendered_html).items():
        prices[item_id] = (f"${value:,.2f}", value, "USD")

    for item in items:
        fields = _price_fields(item, site)
        item_id = walmart_parser.item_id_from_url(item.get("url") or "")
        # Fills gaps only — a price read off the rendered grid is what the
        # shopper is actually shown, while productList's needs the cents
        # heuristic to be interpreted at all. Where both have an answer, prefer
        # the one that needed no guessing.
        if item_id and item_id not in prices:
            prices[item_id] = fields
        title_key = _title_key(item.get("name") or "")
        if title_key and title_key not in by_title:
            by_title[title_key] = fields

    priced = 0
    matched_by_title = 0
    for pos, p in enumerate(products, start=1):
        p.site_rank = pos
        item_id = p.identifier or walmart_parser.item_id_from_url(p.product_url)
        entry = prices.get(item_id or "")
        if not (entry and entry[1] is not None):
            fallback = by_title.get(_title_key(p.title))
            if fallback and fallback[1] is not None:
                entry = fallback
                matched_by_title += 1
        if entry and entry[1] is not None:
            p.price_text, p.price_min, p.currency = entry
            p.price_max = p.price_min
            priced += 1

    warnings: list[str] = []
    if priced < len(products):
        warnings.append(
            f"[{site.label}] {len(products) - priced} of {len(products)} listings "
            "show no price — Walmart serves prices only to a second request, and "
            f"these rows weren't in it. A guessed price is worse than none."
            + (f" ({matched_by_title} matched on title.)" if matched_by_title else "")
        )
    warnings += _assign_normalized_scores(products, site)
    return SiteResult(products=products, warnings=warnings)


async def _zyte_product_list(site: SiteConfig, url: str, zyte: ZyteClient) -> list[dict]:
    return await zyte.extract_product_list(url)


# An empty productList is reported as "no products", not "the fetch failed",
# because Zyte returns 200 either way — so a render race and a genuinely empty
# search look identical. The wording says which is which honestly: it names the
# two possibilities rather than asserting the site has nothing.
_EMPTY_MESSAGE = (
    "No products came back for this search — either the site has no matches "
    "for it, or the page hadn't finished rendering when it was read."
)


async def _product_list_with_empty_retry(
    site: SiteConfig, url: str, zyte: ZyteClient
) -> list[dict]:
    """productList, retried once when it comes back empty.

    IKEA is why. It is a client-rendered search page that Zyte reads after
    rendering, and it took 7-52s to answer across otherwise identical runs — so
    an occasional empty response is a render race rather than an empty
    catalogue. (Probed: four consecutive runs of one query returned 9 products
    every time, while a live search that same day reported none.)

    Only ever one extra request, only on an empty result, so a site that
    genuinely has no match for the query costs one wasted fetch and then says
    so.
    """
    items = await zyte.extract_product_list(url)
    if items:
        return items
    return await zyte.extract_product_list(url)


async def _walmart_next_data(url: str, zyte: ZyteClient) -> tuple[list[Product], str]:
    """Products from the page's own __NEXT_DATA__ blob, plus the rendered HTML.

    Rendered rather than raw, because the blob ships every price zeroed and the
    grid fills them in client side — so the same fetch that yields the products
    also yields the only prices that are guaranteed to belong to *those*
    products. The HTML is handed back so the caller can mine both.
    """
    result = await zyte.extract(url, browser_html=True)
    html = _decode_body(result)
    return (walmart_parser.parse_search_results(html) if html else []), html


def _decode_body(result: dict) -> str:
    body = result.get("httpResponseBody", "")
    if not body:
        return result.get("browserHtml", "") or ""
    import base64

    return base64.b64decode(body).decode("utf-8", errors="replace")


def _normalize_by_position(products: list[Product]) -> None:
    n = len(products)
    for pos, p in enumerate(products, start=1):
        # position 1 -> 1.0, position n -> ~0. Single result -> 1.0.
        p.normalized_score = 1.0 if n == 1 else 1.0 - (pos - 1) / (n - 1)


def _normalize_by_value(products: list[Product], values: list[float | None]) -> None:
    """Min-max scale a numeric signal within this site's own results. Listings
    with no value score 0.0 — below any peer that has one, never a midpoint."""
    present = [v for v in values if v is not None]
    lo, hi = min(present), max(present)
    span = hi - lo
    for p, v in zip(products, values):
        if v is None:
            p.normalized_score = 0.0
        elif span == 0:
            p.normalized_score = 1.0
        else:
            p.normalized_score = (v - lo) / span


def _assign_normalized_scores(products: list[Product], site: SiteConfig) -> list[str]:
    """Collapse each listing's signal to a 0-1 Normalized Score, scaled within
    this site's own result set, and record which signal produced it.

    The site's declared rank_basis is a claim about the URL, not about the data
    that came back — so each branch verifies its signal is actually present and
    degrades (with a warning) when it isn't. A site that claims sold_count but
    returns none must fall back visibly, not score everything 0.
    """
    warnings: list[str] = []
    basis = site.rank_basis

    if basis == "sold_count":
        sold = [p.popularity_score for p in products]  # retail_browser puts sold counts here
        if any(v is not None for v in sold):
            _normalize_by_value(products, sold)
        else:
            warnings.append(
                f"[{site.label}] No sold counts published on these results; "
                "ranked by page order only."
            )
            basis = "relevance"
            _normalize_by_position(products)

    elif basis == "rating":
        for p in products:
            if p.rating is not None and p.review_count is not None:
                p.popularity_score = p.rating * p.review_count
        scores = [p.popularity_score for p in products]
        if any(v is not None for v in scores):
            _normalize_by_value(products, scores)
        else:
            warnings.append(
                f"[{site.label}] No rating data on these results; ranked by page order only."
            )
            basis = "relevance"
            _normalize_by_position(products)

    else:
        # bestseller_sort: the URL carried the site's own best-selling order, so
        # list position IS the ranking. relevance: page order is all there is.
        # Same arithmetic, very different confidence — which is what rank_basis
        # and its weight exist to keep straight.
        _normalize_by_position(products)

    if basis == "relevance" and site.rank_basis == "relevance":
        warnings.append(
            f"[{site.label}] offers no best-selling sort — these are relevance-ordered "
            "and weighted down accordingly."
        )

    for p in products:
        p.rank_basis = basis
    return warnings


# ---------------------------------------------------------------------------
# Cross-site merge (Shared Identifier only) + Combined Rank
# ---------------------------------------------------------------------------

def _fair_truncate(ordered: list[Product], site_order: dict[str, int]) -> list[Product]:
    """Cut to TOP_N without letting the first stores consume the whole budget.

    Results are grouped by store, so a plain `merged[:TOP_N]` slice takes them
    in store order — and with Amazon (40) + Walmart (41) + Temu (19) landing on
    exactly 100, Costco and IKEA were silently dropped from the response
    entirely. The user had selected those stores; returning nothing for them
    while claiming success is precisely the kind of quiet failure the rest of
    this module refuses to make.

    So slots are dealt round-robin across stores — each store contributes its
    next-best listing in turn until the budget runs out. Stores with fewer
    results simply stop contributing and their slack goes to the others.
    Selection is fair; the returned order stays grouped.
    """
    by_site: dict[str, list[Product]] = {}
    for p in ordered:
        by_site.setdefault(p.site, []).append(p)

    picked: list[Product] = []
    round_index = 0
    while len(picked) < TOP_N:
        added = False
        for site_id in sorted(by_site, key=lambda s: site_order.get(s, 99)):
            bucket = by_site[site_id]
            if round_index < len(bucket) and len(picked) < TOP_N:
                picked.append(bucket[round_index])
                added = True
        if not added:
            break
        round_index += 1

    # Restore grouped presentation order after the fair selection.
    picked.sort(key=lambda p: (site_order.get(p.site, 99), ordered.index(p)))
    return picked


def _variant_key(p: Product) -> tuple:
    """Identity of the parent product a listing belongs to, within one site.

    Amazon lists every colour and size of one product as its own row with its
    own ASIN — "Owala FreeSip … 24 oz Denim", "… 24 oz Very Very Dark",
    "… 32 oz Foggy Tide" — which stacked three identical-looking entries at the
    top of the merged list. The tell is that Amazon shares one review count
    across the whole variant family (131,389 on all of them), so review count
    plus the brand/model words at the start of the title identifies the parent.

    Both parts are required: review count alone could collide between unrelated
    products, and the title prefix alone would merge a 24oz and 32oz of
    different products that happen to share a brand.
    """
    head = " ".join(" ".join(p.title.lower().split()).split(" ")[:4])
    if p.review_count:
        return (p.site, p.review_count, head)
    return (p.site, " ".join(p.title.lower().split())[:120])


def _dedupe_within_site(products: list[Product]) -> list[Product]:
    """Collapse a site's repeats of one product into a single row.

    Amazon returns a row per variant — same title, same rating, same
    "20K+ bought in past month" — which filled the top of the merged list with
    three identical Owala entries. Keyed on (site, normalized title) rather than
    identifier, because the variants have *different* ASINs and so are invisible
    to the identifier-only cross-site merge below.

    Deliberately scoped within a single site: collapsing the same title across
    sites would be the fuzzy title matching that CONTEXT.md rules out.
    """
    seen: dict[tuple, Product] = {}
    order: list[tuple] = []
    for p in products:
        key = _variant_key(p)
        current = seen.get(key)
        if current is None:
            seen[key] = p
            order.append(key)
        elif (p.normalized_score or 0) > (current.normalized_score or 0):
            seen[key] = p
    return [seen[k] for k in order]


def _merge_and_rank(products: list[Product]) -> list[Product]:
    """Merge listings that share a confirmed Shared Identifier into one Merged
    Listing (attaching every site's offer as a Seller), then sort the whole
    pool by Normalized Score and assign Combined Rank. Listings with no
    identifier are never merged."""
    products = _dedupe_within_site(products)

    groups: dict[str, list[Product]] = {}
    singletons: list[Product] = []
    for p in products:
        if p.identifier:
            groups.setdefault(p.identifier, []).append(p)
        else:
            singletons.append(p)

    merged: list[Product] = list(singletons)
    for members in groups.values():
        if len(members) == 1:
            merged.append(members[0])
            continue
        # Primary = the member with the strongest Normalized Score.
        primary = max(members, key=lambda m: m.normalized_score or 0.0)
        primary.normalized_score = max(m.normalized_score or 0.0 for m in members)
        primary.sellers = [
            Seller(
                site=m.site,
                seller_name=m.seller_name,
                seller_url=m.seller_url,
                product_url=m.product_url,
                price_text=m.price_text,
                price_min=m.price_min,
                price_max=m.price_max,
                currency=m.currency,
            )
            for m in members
        ]
        merged.append(primary)

    # Default ordering is grouped by store, best-selling first within each.
    #
    # A single interleaved cross-site list answers "what sells best overall",
    # but the actual workflow is per-store — you compare Amazon's top sellers
    # against each other, then Walmart's. Interleaving also silently compares
    # signals of different strength row by row, which the basis weight can only
    # partly compensate for. Grouping keeps each store's ranking internally
    # honest: within one store every row shares a Rank Basis, so the order is a
    # like-for-like comparison.
    #
    # Store order follows the SITES table (Amazon, Walmart, Temu, Costco, IKEA),
    # which is the same order the UI lists them in.
    site_order = {site_id: i for i, site_id in enumerate(SITES)}

    def _within_store(p: Product) -> float:
        weight = RANK_BASIS_WEIGHT.get(p.rank_basis or "relevance", 0.45)
        return (p.normalized_score or 0.0) * weight

    merged.sort(key=lambda p: (site_order.get(p.site, 99), -_within_store(p)))

    top = _fair_truncate(merged, site_order)
    # site_rank = position within this store; combined_rank = position overall.
    per_site: dict[str, int] = {}
    for i, p in enumerate(top, start=1):
        p.combined_rank = i
        per_site[p.site] = per_site.get(p.site, 0) + 1
        p.site_rank = per_site[p.site]
    return top


# ---------------------------------------------------------------------------
# Result Cache (in-memory, query+sites keyed, short TTL — no DB in this app)
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, SearchResponse]] = {}


def _cache_key(query: str, site_ids: list[str]) -> str:
    q = " ".join(query.strip().lower().split())
    return f"{q}|{','.join(sorted(site_ids))}"


def _cache_get(key: str) -> SearchResponse | None:
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, response = hit
    if time.monotonic() - ts > CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return response


def _cache_put(key: str, response: SearchResponse) -> None:
    _CACHE[key] = (time.monotonic(), response)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_sites(sites: str | None) -> list[str]:
    if not sites:
        return ALL_SITES
    requested = [s.strip() for s in sites.split(",") if s.strip()]
    unknown = [s for s in requested if s not in SITES]
    if unknown:
        raise ValueError(f"Unknown site(s): {', '.join(unknown)}. Valid: {', '.join(ALL_SITES)}")
    return requested or ALL_SITES


# --- "Find more", per store ------------------------------------------------
#
# Each store gets its own button, because "more" means something different at
# each one and only the store itself can say whether it has any: Target can open
# another page of its best-selling sort, IKEA returns nine results in total and
# is simply finished. A single global "more" would have to average those into one
# answer and would be wrong at both ends.
#
# Two of the three transports can be asked for a deeper slice:
#
#   via_apify   Ask the actor for `have + MORE_BATCH` rows and return the tail.
#               No actor here accepts an offset, so the earlier rows are fetched
#               (and billed) again — which is why this is a button and not
#               something the app does on its own.
#   via_api     SerpApi walks the same best-selling sort by page. Rainforest is
#               skipped on this path: it has no page parameter, so pretending
#               otherwise would re-return page 1 as though it were page 2.
#
# Zyte's productList has no page parameter either, which leaves IKEA — the only
# site that needs one — honestly out of rows rather than quietly repeating them.
MORE_BATCH = 24
# A ceiling on how deep the button can go. Every press past the first refetches
# everything before it, so the cost of pressing it grows while the number of new
# rows stays flat; past this depth the store is better re-queried than paged.
MORE_MAX_DEPTH = 150


async def more_from_site(query: str, site_id: str, have: int) -> SearchResponse:
    """The next batch of results from ONE store, excluding the `have` already
    shown. An empty `results` means that store has no more to give — the caller
    shows "no more" rather than an error, because being finished is not a fault.
    """
    site = SITES[site_id]
    have = max(0, have)

    if have >= MORE_MAX_DEPTH:
        return SearchResponse(results=[], warnings=[
            f"[{site.label}] Stopped at {have} results — deeper paging costs more "
            f"each time and returns less. Narrow the keyword instead."
        ])

    want = have + MORE_BATCH

    if site.via_apify:
        products, warnings = await apify_retail.fetch_site(site_id, query, max_items=want)
    elif site.via_api and site_id in serpapi_retail.SUPPORTED_SITES:
        # Page numbering is 1-based, and `have` rows have already been shown.
        page = have // MORE_BATCH + 1
        try:
            products, warnings = await serpapi_retail.search(site_id, query, page=page)
        except serpapi_retail.SerpApiError as e:
            return SearchResponse(results=[], warnings=[f"[{site.label}] {e}"])
        # A page request returns only that page, so nothing has been seen before.
        have = 0
    else:
        return SearchResponse(results=[], warnings=[
            f"[{site.label}] This store returns its whole result set in one "
            f"request, so there is nothing further to fetch."
        ])

    if not products:
        return SearchResponse(results=[], warnings=warnings)

    # Site Rank and Normalized Score are both defined relative to a store's own
    # result set, so they're assigned over everything fetched and only then
    # sliced. Scoring the tail on its own would restart the 0-1 scale partway
    # down the list, making row 41 look as strong as row 1.
    for pos, p in enumerate(products, start=1):
        p.site_rank = pos
    warnings = warnings + _assign_normalized_scores(products, site)

    fresh = _dedupe_within_site(products)[have:]
    if not fresh:
        return SearchResponse(results=[], warnings=warnings)

    # Same screening as the first page: without it the extra rows are exactly the
    # accessories and unrelated stock the main search filtered out, since the
    # further down a result list you go the less of it answers the query.
    if settings.claude_relevance_filter:
        screened = await claude_agent.filter_by_relevance(query, fresh)
        fresh = screened.kept
        warnings.extend(screened.warnings)

    return SearchResponse(results=fresh, warnings=warnings)


async def best_seller_search(
    query: str, site_ids: list[str], zyte: ZyteClient | None = None
) -> SearchResponse:
    key = _cache_key(query, site_ids)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    zyte = zyte or ZyteClient()
    site_results = await asyncio.gather(
        *(_fetch_site(SITES[sid], query, zyte) for sid in site_ids)
    )

    all_products: list[Product] = []
    all_warnings: list[str] = []
    for sr in site_results:
        all_products.extend(sr.products)
        all_warnings.extend(sr.warnings)

    # Screen out what the sites returned but the user didn't ask for, BEFORE
    # ranking. A keyword search for "tumbler" comes back with lids, straws,
    # cleaning brushes and unrelated stock, and no scraped field can tell those
    # from a tumbler — only reading the title can. See app/claude_agent.py.
    #
    # Filtering here rather than after _merge_and_rank matters: dedupe, the
    # basis weighting and the round-robin TOP_N deal all then operate on real
    # matches, so a store's share of the budget isn't spent on its accessories.
    # Nothing back-fills the freed slots — TOP_N is a ceiling, not a quota.
    if settings.claude_relevance_filter and all_products:
        screened = await claude_agent.filter_by_relevance(query, all_products)
        all_products = screened.kept
        all_warnings.extend(screened.warnings)

    ranked = _merge_and_rank(all_products)
    response = SearchResponse(results=ranked, warnings=all_warnings)

    # Never cache a failure. A transient Zyte timeout returning zero products
    # would otherwise be served for the full 30-minute TTL, turning one blip
    # into half an hour of a permanently broken-looking query.
    if ranked:
        _cache_put(key, response)
    return response
