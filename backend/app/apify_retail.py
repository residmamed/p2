"""The retail sites reached through Apify actors.

Temu and Costco are here because they defeated every other transport, and the
failures were measured, not assumed:

    Zyte productList   Temu: 0 products in every mode.  Costco: HTTP 520 ban.
    Browserbase        Temu: "Security verification" interstitial.
    Apify (apivault)   Temu: actor ran, parsed 0 — "page may be JS-gated".

These two actors do work, so they're the transport for these sites:

    temu     amit123/temu-products-scraper          -> title, sales_num ("25K+"),
                                                      price_info, image, goods_id
    costco   e-commerce/costco-fast-product-scraper -> name, listPrice, rating,
                                                      reviewsCount, itemNumber

Temu's `sales_num` is the demand signal the app wants — units actually sold —
so Temu ranks on real sales rather than the page order the browser path settled
for. Costco publishes ratings but no sales figures, so it stays relevance-ranked.

The six below were added later, and every claim in this table came out of a live
5-item probe of the actor rather than its README:

    target     automation-lab/target-scraper         rating + reviewCount, and a
                                                     real `sort=bestselling`
    ebay       automation-lab/ebay-scraper           price + seller, no product
                                                     rating (eBay rates sellers)
    etsy       automation-lab/etsy-scraper           rating but NO review count,
                                                     and a malformed price
    homedepot  crawlerbros/homedepot-scraper         price only, plus a real
                                                     `sortBy=top_sellers`
    bestbuy    piotrv1001/bestbuy-listings-scraper   rating + reviewsCount, price
                                                     nested under priceDomain
    wayfair    piotrv1001/wayfair-listings-scraper   rating + reviewCount + price

Two findings from that probe are worth keeping in view, because both look like
bugs in this file when they are actually the sites:

  * Target and Best Buy already return rating and review count in search
    results, so the separate "reviews" actors for those two stores were dropped
    before being wired — they would have cost one extra billed call per product
    to fetch a number we were already given.
  * crawlerbros/bestbuy-scraper was tried for Best Buy first and returned a
    `{"type": "bestbuy_error", "reason": "no_results"}` record instead of
    products — Best Buy's bot challenge had persisted across its retries. The
    piotrv1001 actor returned 10 clean rows for the same keyword, so that's the
    one wired. Best Buy is the most likely of these six to come back empty.

Uses the same APIFY_TOKEN as Pinterest and Google Lens.
"""
import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote_plus

import httpx

from . import credentials
from .models import Product
from .product_images import best_image

APIFY_RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
TIMEOUT_SECONDS = 300.0
# Costco's actor rejects anything below 30; Temu's is uncapped.
DEFAULT_MAX_ITEMS = 40


class ApifyRetailError(Exception):
    pass


def is_configured() -> bool:
    return bool(credentials.APIFY)


def _num(value: Any, cast: Callable):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


# --- Temu -----------------------------------------------------------------

SOLD_RE = re.compile(r"([\d][\d.,]*)\s*([KkMm])?\+?", re.I)


def _parse_sales_num(raw: Any) -> int | None:
    """"25K+" -> 25000. Temu's headline metric and the reason this site can be
    ranked on real demand instead of page order."""
    if raw is None:
        return None
    match = SOLD_RE.match(str(raw).strip())
    if not match:
        return None
    value = _num(match.group(1).replace(",", ""), float)
    if value is None:
        return None
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    return int(value)


def _temu_price(item: dict) -> tuple[str | None, float | None]:
    """Temu splits the price into fragments: ["$", "31", ".94", ""]."""
    info = item.get("price_info") or {}
    parts = info.get("split_price_text") if isinstance(info, dict) else None
    if isinstance(parts, list) and parts:
        text = "".join(str(p) for p in parts).strip()
        digits = re.sub(r"[^\d.]", "", text)
        return (text or None), _num(digits, float)
    return None, None


def _temu_rating(item: dict) -> tuple[float | None, int | None]:
    """Temu tucks rating and review count inside `comment`, not at the top
    level: {"goods_score": 4.7, "comment_num_tips": "372"}.

    comment_num_tips is a display string and uses the same K/M abbreviation as
    sales_num ("1.2K"), so it goes through the same parser rather than int().
    """
    comment = item.get("comment")
    if not isinstance(comment, dict):
        return None, None
    rating = _num(comment.get("goods_score"), float)
    reviews = _parse_sales_num(comment.get("comment_num_tips"))
    return rating, reviews


def _temu_product(item: dict) -> Product | None:
    title = (item.get("title") or item.get("goods_name") or "").strip()
    url = item.get("link_url") or ""
    if not title or not url:
        return None
    rating, review_count = _temu_rating(item)

    image = item.get("image")
    image_url = image.get("url") if isinstance(image, dict) else (image if isinstance(image, str) else None)
    if not image_url:
        image_url = item.get("thumb_url")

    price_text, price_min = _temu_price(item)

    return Product(
        site="temu",
        title=title[:300],
        product_url=url,
        image_url=best_image(image_url, site="temu"),
        price_text=price_text,
        price_min=price_min,
        price_max=price_min,
        currency="USD" if price_min is not None else None,
        seller_name="Temu",
        rating=rating,
        review_count=review_count,
        # Units sold — the demand signal bestsellers.py ranks Temu on.
        popularity_score=_parse_sales_num(item.get("sales_num") or item.get("sales_tip")),
        identifier=str(item["goods_id"]) if item.get("goods_id") else None,
    )


# --- Costco ---------------------------------------------------------------

def _costco_product(item: dict) -> Product | None:
    title = (item.get("name") or item.get("itemName") or "").strip()
    url = item.get("productUrl") or ""
    if not title or not url:
        return None

    price = _num(item.get("listPrice") or item.get("minPrice"), float)
    images = item.get("images")
    image_url = item.get("image") or (images[0] if isinstance(images, list) and images else None)

    return Product(
        site="costco",
        title=title[:300],
        product_url=url,
        image_url=best_image(image_url if isinstance(image_url, str) else None, site="costco"),
        price_text=f"${price:,.2f}" if price is not None else None,
        price_min=price,
        price_max=price,
        currency=item.get("currencyCode") or ("USD" if price is not None else None),
        seller_name="Costco",
        rating=_num(item.get("rating"), float),
        review_count=_num(item.get("reviewsCount"), int),
        identifier=str(item["itemNumber"]) if item.get("itemNumber") else None,
    )


# --- Target, eBay, Etsy, Home Depot, Best Buy, Wayfair --------------------
#
# Each actor names the same handful of concepts differently, so rather than six
# near-identical functions, the differences are declared as field names and the
# one builder below reads them. Anything genuinely peculiar to a site — Etsy's
# price, Best Buy's nested price — gets a function, because a field name can't
# express it.

def _usd(price: float | None) -> str | None:
    return f"${price:,.2f}" if price is not None else None


def _first_str(item: dict, *keys: str) -> str | None:
    """First key holding a usable string. Several of these actors return "" for
    a field they couldn't fill, which is not the same as absent and must not
    become a title or a URL."""
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# Etsy's actor concatenates the price with a repeat of its own leading digits:
# a $19.99 listing arrives as "19.9919", $29.50 as "29.5029", $3.80 as "3.803".
# Verified across every row of the probe. Taking the leading amount recovers the
# real price; a bare integer ("7") is already correct and passes through.
ETSY_PRICE_RE = re.compile(r"^\s*(\d+(?:\.\d{2})?)")


def _etsy_price(raw: Any) -> float | None:
    if raw is None:
        return None
    match = ETSY_PRICE_RE.match(str(raw))
    return _num(match.group(1), float) if match else None


def _bestbuy_price(item: dict) -> float | None:
    """Best Buy nests price in a priceDomain object and quotes both the list and
    the current price. currentPrice is what a buyer pays, so it's the one that
    should drive price filters and the Opportunity Score."""
    domain = item.get("priceDomain")
    if not isinstance(domain, dict):
        return _num(item.get("currentPrice"), float)
    return _num(domain.get("currentPrice") or domain.get("regularPrice"), float)


@dataclass(frozen=True)
class FieldMap:
    """Where one actor keeps each field the app needs."""
    title: tuple[str, ...]
    url: tuple[str, ...]
    image: tuple[str, ...]
    identifier: tuple[str, ...]
    seller_name: str
    price: tuple[str, ...] = ()
    price_fn: Callable[[dict], float | None] | None = None
    rating: tuple[str, ...] = ()
    review_count: tuple[str, ...] = ()
    seller_field: tuple[str, ...] = ()


def _mapped_product(site: str, fields: FieldMap, item: dict) -> Product | None:
    title = _first_str(item, *fields.title)
    url = _first_str(item, *fields.url)
    # A row without a title or a link isn't a product the UI can show or the
    # sourcing pipeline can follow, so it's dropped rather than half-rendered.
    if not title or not url:
        return None

    # Sponsored placements are ads, not search results, and letting one hold a
    # Site Rank would put a paid slot above an organic best seller.
    if item.get("isSponsored") is True:
        return None

    if fields.price_fn is not None:
        price = fields.price_fn(item)
    else:
        price = next((_num(item.get(k), float) for k in fields.price if item.get(k) is not None), None)

    images = item.get("images")
    from_list = images[0] if isinstance(images, list) and images else None
    image_url = best_image(*(item.get(k) for k in fields.image), from_list, site=site)

    rating = next((_num(item.get(k), float) for k in fields.rating if item.get(k) is not None), None)
    review_count = next((_num(item.get(k), int) for k in fields.review_count if item.get(k) is not None), None)

    # Every store here rates on 1-5 stars, so a 0 is not a rating — it's how
    # Best Buy encodes "no reviews yet" (seen live: rating 0.0 alongside
    # reviewCount 0). Kept as 0.0 it would sort as the worst-reviewed product in
    # the grid and drag down the market average, both of which say something
    # about the product that the store never said.
    if rating is not None and rating <= 0:
        rating = None
        if not review_count:
            review_count = None

    return Product(
        site=site,
        title=title[:300],
        product_url=url,
        image_url=image_url,
        price_text=_usd(price),
        price_min=price,
        price_max=price,
        currency=_first_str(item, "currency") or ("USD" if price is not None else None),
        seller_name=_first_str(item, *fields.seller_field) or fields.seller_name,
        rating=rating,
        review_count=review_count,
        identifier=_first_str(item, *fields.identifier),
    )


FIELD_MAPS: dict[str, FieldMap] = {
    "target": FieldMap(
        title=("title",), url=("url",), image=("thumbnail",), identifier=("tcin",),
        seller_name="Target",
        # priceString is "$29.99"; price is the number. The bestselling row is
        # routinely the one with price=None — a multi-variant listing Target
        # prices per variant — so it stays in the grid unpriced rather than
        # being dropped for lacking a field the site never published.
        price=("price",),
        rating=("rating",), review_count=("reviewCount",),
    ),
    "ebay": FieldMap(
        title=("title",), url=("url",), image=("thumbnail",), identifier=("itemId",),
        # eBay listings are sold by individual sellers, so the seller name is
        # real data here, not a store label.
        seller_name="eBay", seller_field=("sellerName",),
        price=("price",),
        # Deliberately empty: eBay's `rating`/`reviewCount` are absent, and
        # sellerFeedbackPercent rates the *seller across all sales*, not this
        # product. Mapping it to `rating` would put "99.5% positive seller" in
        # a column the UI presents as the product's rating.
    ),
    "etsy": FieldMap(
        title=("name",), url=("url",), image=("imageUrl",), identifier=("listingId",),
        seller_name="Etsy", seller_field=("shop",),
        price_fn=lambda item: _etsy_price(item.get("price")),
        # Etsy returns a rating with no review count whatsoever. The rating is
        # carried because it's real, and review_count stays None because
        # inventing one would let an unbacked 5.0 outrank an evidenced 4.7.
        rating=("rating",),
    ),
    "homedepot": FieldMap(
        title=("title",), url=("url",), image=("imageUrl",), identifier=("itemId",),
        seller_name="Home Depot",
        price=("price",),
        # No rating or review count in this actor's output at all. Home Depot
        # ranks on its own top_sellers sort instead, which is a stronger signal
        # than a rating would have been.
    ),
    "bestbuy": FieldMap(
        title=("name",), url=("productUrl", "url"), image=("imageUrl",), identifier=("sku",),
        seller_name="Best Buy",
        price_fn=_bestbuy_price,
        rating=("rating",), review_count=("reviewsCount", "reviewCount"),
    ),
    "wayfair": FieldMap(
        title=("name",), url=("url",), image=("leadImage",), identifier=("sku",),
        seller_name="Wayfair",
        price=("price",),
        rating=("rating",), review_count=("reviewCount",),
    ),
}


def _mapped(site: str) -> Callable[[dict], Product | None]:
    fields = FIELD_MAPS[site]
    return lambda item: _mapped_product(site, fields, item)


# Best Buy's actor reports a blocked run as a dataset record rather than a
# failure, so an unparsed one would surface as a product with no title. Caught
# here so fetch_site can say the site blocked us instead of silently thinning.
def _is_error_record(item: dict) -> bool:
    return isinstance(item.get("type"), str) and item["type"].endswith("_error")


# Target's and eBay's actors cap results by *page* as well as by count, so a
# request for more products than one page holds silently returns one page. Both
# paginate at roughly 24 rows. The 5-page ceiling is the actors' own default max.
ROWS_PER_SEARCH_PAGE = 24
MAX_SEARCH_PAGES = 5


def _pages_for(count: int) -> int:
    """How many search pages to allow for a request of `count` products.

    Deliberately 1 for anything up to an ordinary search: these actors bill per
    product scraped, so opening a second page doubles the cost of every search
    to fill in rows below the fold that most searches never look at. Only the
    per-store "find more" button asks for more than DEFAULT_MAX_ITEMS, and only
    then is another page worth paying for.
    """
    if count <= DEFAULT_MAX_ITEMS:
        return 1
    return max(1, min(MAX_SEARCH_PAGES, -(-count // ROWS_PER_SEARCH_PAGE)))


def _search_url(site: str, query: str) -> str:
    """The search URL for actors that take startUrls instead of a keyword."""
    return {
        "bestbuy": f"https://www.bestbuy.com/site/searchpage.jsp?st={quote_plus(query)}",
        "wayfair": f"https://www.wayfair.com/keyword.php?keyword={quote_plus(query)}",
    }[site]


# --- wiring ---------------------------------------------------------------

@dataclass(frozen=True)
class ActorConfig:
    site: str
    label: str
    actor: str
    build_input: Callable[[str, int], dict]
    to_product: Callable[[dict], Product | None]


ACTORS: dict[str, ActorConfig] = {
    "temu": ActorConfig(
        site="temu",
        label="Temu",
        actor="amit123~temu-products-scraper",
        build_input=lambda q, n: {"searchQueries": [q], "currency": "USD", "maxResults": n},
        to_product=_temu_product,
    ),
    "costco": ActorConfig(
        site="costco",
        label="Costco",
        actor="e-commerce~costco-fast-product-scraper",
        # This actor takes a bare keyword and builds the search URL itself;
        # passing a Costco search URL as startUrls returned nothing.
        build_input=lambda q, n: {"keyword": q, "maxProductsPerUrl": max(n, 30)},
        to_product=_costco_product,
    ),
    "target": ActorConfig(
        site="target",
        label="Target",
        actor="automation-lab~target-scraper",
        # sort=bestselling is Target's own best-selling order and it works: the
        # probe for "water bottle" came back Owala first, which is in fact the
        # best seller. maxSearchPages is pinned to 1 because this actor bills
        # per product scraped and a second page doubles the cost of a search
        # whose first page already exceeds what the grid shows.
        build_input=lambda q, n: {
            "searchQueries": [q],
            "maxProductsPerSearch": n,
            # One page for an ordinary search — this actor bills per product
            # scraped, so a second page doubles the cost of a search whose first
            # page already exceeds what the grid shows. Only the per-store "find
            # more" button asks for enough rows to open another one.
            "maxSearchPages": _pages_for(n),
            "sort": "bestselling",
        },
        to_product=_mapped("target"),
    ),
    "ebay": ActorConfig(
        site="ebay",
        label="eBay",
        actor="automation-lab~ebay-scraper",
        # eBay's sort enum offers best_match, ending_soonest, newly_listed and
        # the two price orders — no best-selling option, so best_match is the
        # honest choice and bestsellers.py marks eBay relevance-ranked. The
        # actor does expose a soldCount field, which would have been a
        # sold_count signal, but it came back empty on every probed row.
        build_input=lambda q, n: {
            "searchQueries": [q],
            "maxProductsPerSearch": n,
            "maxSearchPages": _pages_for(n),
            "sort": "best_match",
            "listingType": "buy_it_now",  # auctions have no stable price to rank or compare
        },
        to_product=_mapped("ebay"),
    ),
    "etsy": ActorConfig(
        site="etsy",
        label="Etsy",
        # Note the singular searchQuery — this actor is the one that doesn't
        # take a list.
        actor="automation-lab~etsy-scraper",
        build_input=lambda q, n: {
            "searchQuery": q,
            "maxItems": n,
            "currency": "USD",
            "sort": "most_relevant",  # no best-selling or most-reviewed option exists
            "excludeDigitalDownloads": True,  # a downloadable PDF has no supplier to source
        },
        to_product=_mapped("etsy"),
    ),
    "homedepot": ActorConfig(
        site="homedepot",
        label="Home Depot",
        # maplerope44/home-depot-product-lookup was the more popular actor by an
        # order of magnitude but takes a productId, not a keyword — it can't
        # answer a search at all. This one takes searchQuery.
        actor="crawlerbros~homedepot-scraper",
        build_input=lambda q, n: {
            "searchQuery": q,
            "maxItems": n,
            "sortBy": "top_sellers",
            "includeSponsored": False,
        },
        to_product=_mapped("homedepot"),
    ),
    "bestbuy": ActorConfig(
        site="bestbuy",
        label="Best Buy",
        actor="piotrv1001~bestbuy-listings-scraper",
        # startUrls here is a list of plain strings, unlike Wayfair's list of
        # {"url": ...} objects. Both were probed; neither accepts the other's
        # shape. No sort parameter exists, hence relevance in bestsellers.py.
        build_input=lambda q, n: {"startUrls": [_search_url("bestbuy", q)], "maxItems": n},
        to_product=_mapped("bestbuy"),
    ),
    "wayfair": ActorConfig(
        site="wayfair",
        label="Wayfair",
        # mscraper/wayfair-scraper is the better-known Wayfair actor but rents
        # at a flat $20/month; this one bills $0.0015 per product, which is why
        # it's here. It takes only startUrls, so the keyword search URL is built
        # by us — the form Wayfair's own search box produces.
        actor="piotrv1001~wayfair-listings-scraper",
        build_input=lambda q, n: {
            "startUrls": [{"url": _search_url("wayfair", q)}],
            "maxResults": n,
        },
        to_product=_mapped("wayfair"),
    ),
}


async def fetch_site(site: str, query: str, max_items: int = DEFAULT_MAX_ITEMS) -> tuple[list[Product], list[str]]:
    """Run one site's actor and map its dataset onto Product. Never raises."""
    config = ACTORS.get(site)
    if config is None:
        return [], [f"No Apify actor configured for site {site!r}"]
    if not is_configured():
        return [], [f"[{config.label}] Apify not configured — set APIFY_TOKEN."]

    url = APIFY_RUN_URL.format(actor=config.actor)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                params={"token": credentials.APIFY.next(), "timeout": int(TIMEOUT_SECONDS)},
                json=config.build_input(query, max_items),
            )
    except httpx.HTTPError as e:
        return [], [f"[{config.label}] Apify request failed ({type(e).__name__})."]

    if response.status_code not in (200, 201):
        return [], [f"[{config.label}] Apify returned {response.status_code}: {response.text[:150]}"]

    try:
        items = response.json()
    except json.JSONDecodeError:
        return [], [f"[{config.label}] Apify returned a non-JSON body."]
    if not isinstance(items, list):
        return [], [f"[{config.label}] Apify returned no dataset items."]

    records = [i for i in items if isinstance(i, dict)]

    # A run the site blocked reports itself as a record, not as an HTTP failure.
    # Passing its own message through beats the generic line below, because it
    # distinguishes "the bot challenge won" from "this keyword has no results".
    blocked = next((r for r in records if _is_error_record(r)), None)
    if blocked is not None:
        reason = blocked.get("message") or blocked.get("reason") or "no products returned"
        return [], [f"[{config.label}] {reason}"]

    products = [p for p in (config.to_product(i) for i in records) if p]
    if not products:
        return [], [
            f"[{config.label}] The actor ran but returned no usable products — "
            "the site may be blocking it today."
        ]
    return products, []


async def fetch_sites(site_ids: list[str], query: str, concurrency: int = 2) -> tuple[list[Product], list[str]]:
    sem = asyncio.Semaphore(concurrency)

    async def _one(sid: str):
        async with sem:
            return await fetch_site(sid, query)

    results = await asyncio.gather(*(_one(s) for s in site_ids if s in ACTORS))
    products: list[Product] = []
    warnings: list[str] = []
    for p, w in results:
        products.extend(p)
        warnings.extend(w)
    return products, warnings
