"""Fill in price/rating/review/image data that a site publishes on its product
pages but not in its search results.

Two mechanisms, because two different problems:

  * `enrich()` parses schema.org JSON-LD out of the page. Cheap and fast, and
    the right tool when the target site is known to ship it. Used for IKEA.
  * `enrich_from_product_pages()` runs Zyte's AI product extraction instead.
    Slower and dearer per page, but it needs no site-specific knowledge, which
    is the only workable option for Google Lens results — those land on
    whatever host Google found, from Target to a regional grocery chain.

IKEA is the case that forced the first. Its search results carry no rating data
at all, but every product page ships standard schema.org JSON-LD:

    {"@type":"Product","aggregateRating":{"ratingValue":"3.8","reviewCount":"660"},
     "image":[{"contentUrl":"...__0985523_pe816659_s5.jpg","height":"2000px"}]}

(An earlier check concluded IKEA had no ratings anywhere — that was a bad fetch
against a URL that redirected, not a real absence.)

One request per product is only affordable because IKEA returns 3-4 results for
a typical query. ENRICH_LIMIT keeps that true if a query ever returns more.
JSON-LD is a cross-site standard, so this works for any site that ships it.
"""
import asyncio
import base64
import json
import re

from .models import Product
from .product_images import best_image
from .zyte_client import ZyteClient

LD_JSON_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)

# The JSON-LD is server-rendered, so the cheap fetch mode carries it: measured
# on an IKEA product page, httpResponseBody returned identical rating/review/
# image data in 1.9s where browserHtml took 35.7s. That 19x difference is what
# makes enriching a whole result set affordable instead of just the first few.
#
# The limit was 8, which silently left most of a 24-product IKEA result set
# without stars — invisible on narrow queries that return only 3-4 items.
ENRICH_LIMIT = 60
ENRICH_CONCURRENCY = 8


def _walk(node, depth: int = 0):
    """Yield every dict in a JSON-LD document — @graph nesting and arrays mean
    the Product node is rarely at the top level."""
    if depth > 8:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value, depth + 1)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, depth + 1)


def _number(value, cast):
    try:
        return cast(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_product_page(html: str) -> dict:
    """Pull {rating, review_count, image_url} out of a product page's JSON-LD.

    Pure function — testable against a saved fixture with no network.
    """
    out: dict = {}
    for match in LD_JSON_RE.finditer(html):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

        for node in _walk(data):
            rating_node = node.get("aggregateRating")
            if isinstance(rating_node, dict) and "rating" not in out:
                rating = _number(rating_node.get("ratingValue"), float)
                count = _number(
                    rating_node.get("reviewCount") or rating_node.get("ratingCount"), int
                )
                if rating is not None or count is not None:
                    out["rating"] = rating
                    out["review_count"] = count

            if "image_url" not in out and node.get("@type") == "Product":
                image = node.get("image")
                if isinstance(image, list) and image:
                    first = image[0]
                    url = first.get("contentUrl") if isinstance(first, dict) else first
                elif isinstance(image, dict):
                    url = image.get("contentUrl")
                else:
                    url = image
                if isinstance(url, str) and url.startswith("http"):
                    out["image_url"] = url

        if "rating" in out and "image_url" in out:
            break
    return out


async def _fetch_html(url: str, zyte: ZyteClient) -> str:
    """Cheap mode first, rendered browser only if it yields nothing usable.

    Same cost ladder the Made-in-China scraper uses. Worth the fallback because
    a site that renders its JSON-LD client-side would otherwise silently return
    no ratings at all.
    """
    for browser in (False, True):
        try:
            result = await zyte.extract(
                url,
                browser_html=browser,
                http_response_body=not browser,
                max_retries=1,
            )
        except Exception:  # noqa: BLE001 - enrichment is best-effort
            continue

        if browser:
            html = result.get("browserHtml") or ""
        else:
            body = result.get("httpResponseBody") or ""
            html = base64.b64decode(body).decode("utf-8", "replace") if body else ""

        if html and LD_JSON_RE.search(html):
            return html
    return ""


async def _enrich_one(product: Product, zyte: ZyteClient, sem: asyncio.Semaphore) -> None:
    async with sem:
        html = await _fetch_html(product.product_url, zyte)
    if not html:
        return

    data = parse_product_page(html)
    # Never overwrite a value the search results already provided.
    if product.rating is None and data.get("rating") is not None:
        product.rating = data["rating"]
    if product.review_count is None and data.get("review_count") is not None:
        product.review_count = data["review_count"]
    if data.get("image_url"):
        product.image_url = best_image(data["image_url"], product.image_url, site=product.site)


async def enrich(products: list[Product], zyte: ZyteClient) -> list[str]:
    """Enrich up to ENRICH_LIMIT products in place. Returns warnings."""
    targets = [p for p in products if p.product_url][:ENRICH_LIMIT]
    if not targets:
        return []

    sem = asyncio.Semaphore(ENRICH_CONCURRENCY)
    await asyncio.gather(
        *(_enrich_one(p, zyte, sem) for p in targets), return_exceptions=True
    )

    got = sum(1 for p in targets if p.rating is not None)
    if not got:
        return ["Rating data could not be read from these product pages."]

    warnings: list[str] = []
    if len(products) > ENRICH_LIMIT:
        warnings.append(
            f"Ratings fetched for the top {ENRICH_LIMIT} of {len(products)} listings "
            "(one request each); the rest show no stars rather than a guess."
        )
    missing = len(targets) - got
    if missing:
        # Some products genuinely have no reviews yet — distinguish that from a
        # systemic failure so a half-empty column isn't read as a bug.
        warnings.append(
            f"{missing} of {len(targets)} listings publish no rating yet — shown without stars."
        )
    return warnings


# ---------------------------------------------------------------------------
# Zyte AI product extraction — for results whose host we know nothing about
# ---------------------------------------------------------------------------

# Google Lens answers with 65 results (25 exact + 40 visual) and a price on
# barely a sixth of them, no ratings at all. One Zyte request per page is what
# fills that in, so the limit is a direct latency/cost trade.
#
# Every page in the batch is fetched at once, which makes wall time the slowest
# single page rather than a multiple of it. That matters more than it sounds:
# these pages range from 13s to 88s, so running 24 of them twelve-at-a-time
# took 81s AND lost the slow half to a 40s cut-off. Starting all 24 together
# under a 60s cap is both faster and more complete.
PRODUCT_PAGE_LIMIT = 24
PRODUCT_PAGE_CONCURRENCY = 24
PAGE_TIMEOUT_SECONDS = 60.0

# Zyte's AI extraction returns a best-guess `product` for pages that aren't
# products at all (404s, category redirects); its confidence score is the only
# signal that guess is bogus. Same threshold the extension bridge uses.
MIN_CONFIDENCE = 0.3

# Lens routinely returns social and video pages — 16 of 65 on a measured search.
# They have no price or rating to find, so fetching them would spend a request
# each to learn nothing.
NON_RETAIL_HOSTS = (
    "tiktok.com", "instagram.com", "facebook.com", "youtube.com", "youtu.be",
    "pinterest.com", "twitter.com", "x.com", "reddit.com", "threads.net",
)

PRICE_TEXT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)")


def _is_retail_host(url: str) -> bool:
    host = url.split("/")[2].lower() if url.count("/") >= 2 else ""
    return not any(host == h or host.endswith("." + h) for h in NON_RETAIL_HOSTS)


def _price_number(text: str | None) -> float | None:
    """Pull a number out of a price string like "$24.99" or "US $31.99"."""
    if not text:
        return None
    m = PRICE_TEXT_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def apply_zyte_product(product: Product, data: dict) -> bool:
    """Map Zyte's automatic product extraction onto a Product in place.

    Returns False when the extraction was too low-confidence to trust, leaving
    the product untouched.

    Price is the one field taken from the page even when the caller already had
    one: Lens's price comes from Google's cached snippet, while Zyte's is read
    off the live page. Rating and review count are only ever filled in, never
    overwritten, matching enrich() above.
    """
    if (data.get("metadata") or {}).get("probability", 0) < MIN_CONFIDENCE:
        return False

    price = _price_number(data.get("price"))
    if price is not None:
        currency = data.get("currencyRaw") or data.get("currency")
        product.price_min = price
        product.price_max = price
        product.currency = currency
        sep = "" if (currency and not currency[-1].isalnum()) else " "
        product.price_text = f"{currency}{sep}{price:.2f}" if currency else f"{price:.2f}"
    elif product.price_min is None:
        # No page price, but the caller's snippet had one — at least make it
        # numeric so the Market Snapshot and margin maths can use it.
        product.price_min = product.price_max = _price_number(product.price_text)

    rating_node = data.get("aggregateRating") or {}
    if product.rating is None:
        product.rating = _number(rating_node.get("ratingValue"), float)
    if product.review_count is None:
        product.review_count = _number(rating_node.get("reviewCount"), int)

    image = (data.get("mainImage") or {}).get("url")
    if image:
        product.image_url = best_image(image, product.image_url, site=product.site)

    # A GTIN or MPN is a real Shared Identifier; sku and productId are private
    # to one site, so they are deliberately not read here.
    if product.identifier is None:
        for key in ("gtin", "mpn"):
            value = data.get(key)
            if isinstance(value, list):
                value = next((v for v in value if v), None)
            if isinstance(value, dict):
                value = value.get("value")
            if value:
                product.identifier = f"{key}:{value}"
                break
    return True


# The UI shows either every exact match, or — when there are none — the closest
# handful of visual matches. Whichever branch it takes, those rows are the ones
# that need prices, so the budget reserves a slice for the visual matches rather
# than letting 25 exact ones consume all of it. Without the reserve, the
# fallback grid rendered the only cards the user would see with no price and no
# rating: precisely the results the enrichment exists to fill in.
VISUAL_MATCH_RESERVE = 5


def _budget(candidates: list[Product]) -> list[Product]:
    """Pick which pages to spend a request on, exact matches first but never
    all of them. Order within each group is Lens's own ranking."""
    exact = [p for p in candidates if p.site == "google_lens_exact"]
    visual = [p for p in candidates if p.site != "google_lens_exact"]

    visual_slots = min(VISUAL_MATCH_RESERVE, len(visual))
    chosen = exact[: PRODUCT_PAGE_LIMIT - visual_slots] + visual[:visual_slots]
    # Whatever the other group didn't need goes back to the one that can use it.
    if len(chosen) < PRODUCT_PAGE_LIMIT:
        spare = PRODUCT_PAGE_LIMIT - len(chosen)
        taken = set(id(p) for p in chosen)
        chosen += [p for p in exact + visual if id(p) not in taken][:spare]
    return chosen


async def _enrich_one_via_zyte(product: Product, zyte: ZyteClient, sem: asyncio.Semaphore) -> None:
    async with sem:
        try:
            data = await zyte.extract_product(product.product_url)
        except Exception:  # noqa: BLE001 - enrichment is best-effort
            return
    if data:
        apply_zyte_product(product, data)


async def enrich_from_product_pages(
    products: list[Product], zyte: ZyteClient | None = None
) -> list[str]:
    """Fill in price/rating/reviews for arbitrary-host results, in place.

    Built for Google Lens, whose results carry a title, a link and a picture and
    almost nothing else. Best-effort throughout: a host that blocks Zyte, times
    out or simply publishes no rating leaves that product as it was rather than
    failing the search.
    """
    zyte = zyte or ZyteClient(timeout=PAGE_TIMEOUT_SECONDS)

    # Free, and it applies to every result rather than just the fetched ones:
    # the caller's own price arrives as a string ("$24.99"), which the Market
    # Snapshot and margin maths can't use until it's a number.
    for p in products:
        if p.price_min is None:
            p.price_min = p.price_max = _price_number(p.price_text)

    candidates = [p for p in products if p.product_url and _is_retail_host(p.product_url)]
    skipped_social = len(products) - len(candidates)
    targets = _budget(candidates)
    if not targets:
        return []

    had_price = sum(1 for p in targets if p.price_min is not None)
    sem = asyncio.Semaphore(PRODUCT_PAGE_CONCURRENCY)
    await asyncio.gather(
        *(_enrich_one_via_zyte(p, zyte, sem) for p in targets), return_exceptions=True
    )

    priced = sum(1 for p in targets if p.price_min is not None)
    rated = sum(1 for p in targets if p.rating is not None or p.review_count is not None)

    warnings: list[str] = []
    if priced == had_price and not rated:
        warnings.append(
            f"Could not read price or rating data from any of these {len(targets)} pages."
        )
        return warnings

    if len(candidates) > PRODUCT_PAGE_LIMIT:
        warnings.append(
            f"Price and rating data fetched for the top {PRODUCT_PAGE_LIMIT} of "
            f"{len(candidates)} results (one page request each); the rest show what "
            "the image search itself returned."
        )
    if rated < len(targets):
        # Plenty of retailers publish no rating at all — say so, so a sparse
        # column reads as the sites' doing rather than a broken fetch.
        warnings.append(
            f"{len(targets) - rated} of {len(targets)} pages publish no rating — "
            "shown without stars rather than a guessed value."
        )
    if skipped_social:
        warnings.append(
            f"{skipped_social} result(s) are social or video pages with no product "
            "data to fetch."
        )
    return warnings
