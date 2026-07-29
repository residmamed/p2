"""Walmart search results from the page's own __NEXT_DATA__ blob.

Zyte's productList reaches Walmart fine but returns only name/price/image/url —
no rating, no review count — which left Walmart rows blank next to Amazon's
Rainforest data. The page itself carries all of it: Walmart is a Next.js app and
ships its full search model as JSON at

    props.pageProps.initialData.searchResult.itemStacks[].items[]

with averageRating, numberOfReviews, usItemId (a real Shared Identifier),
sellerName and an explicit sponsored flag. Parsing that is both cheaper (plain
httpResponseBody, no AI extraction) and strictly richer than productList.

Structure is walked defensively rather than indexed: Walmart reshapes this blob
regularly, and a missing field should cost one attribute, not the whole site.
"""
import json
import re
from typing import Any, Optional

from ..models import Product
from ..product_images import best_image

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)
BASE_URL = "https://www.walmart.com"

# Walmart prices arrive as dollars here (unlike productList, which sometimes
# reports cents) — but guard anyway, since a 100x price error is silent and
# poisons the margin calculator downstream.
CENTS_GUARD_THRESHOLD = 1000


def _find_items(node: Any, depth: int = 0) -> list[dict]:
    """Locate the search-result item list wherever Walmart has moved it to.

    Recognises the list by its contents — dicts carrying both a name and a
    usItemId — rather than by a fixed path, so a reshuffle of the surrounding
    keys doesn't break extraction.
    """
    if depth > 12:
        return []
    if isinstance(node, list):
        if node and isinstance(node[0], dict) and "usItemId" in node[0] and "name" in node[0]:
            return [n for n in node if isinstance(n, dict)]
        for child in node[:20]:
            found = _find_items(child, depth + 1)
            if found:
                return found
    elif isinstance(node, dict):
        for child in node.values():
            found = _find_items(child, depth + 1)
            if found:
                return found
    return []


def _price(item: dict) -> tuple[Optional[str], Optional[float]]:
    price = item.get("price")
    value: Optional[float] = None
    if isinstance(price, (int, float)):
        value = float(price)
    elif isinstance(price, dict):
        for key in ("price", "currentPrice", "linePrice"):
            candidate = price.get(key)
            if isinstance(candidate, (int, float)):
                value = float(candidate)
                break
    if value is None:
        info = item.get("priceInfo") or {}
        current = info.get("currentPrice") if isinstance(info, dict) else None
        if isinstance(current, dict) and isinstance(current.get("price"), (int, float)):
            value = float(current["price"])

    # Walmart ships this blob with prices zeroed — measured: 0 of 40 products
    # carry one on a live search, because the grid fetches them client-side.
    # A $0.00 price is worse than none: it sails through every "has a price"
    # check and then drags the Market Snapshot's median and the margin
    # calculator to zero. Absent is the truthful answer.
    if value is None or value == 0:
        return None, None
    if value >= CENTS_GUARD_THRESHOLD and value == int(value):
        value = value / 100
    return f"${value:,.2f}", value


def _number(value: Any, cast) -> Any:
    try:
        return cast(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _rating_fields(item: dict) -> tuple[Optional[float], Optional[int]]:
    """Rating and review count, from whichever of the two shapes this item uses.

    Walmart ships both a flat `averageRating`/`numberOfReviews` pair and a
    nested `rating: {averageRating, numberOfReviews}` object, and not every item
    carries both — measured on one live search, the flat field was present on
    32 of 42 items while the nested object was present on 40. Reading only the
    flat pair silently dropped the stars off a fifth of the rows, which is
    exactly the "Walmart shows nothing for reviews" symptom.
    """
    rating = _number(item.get("averageRating"), float)
    reviews = _number(item.get("numberOfReviews"), int)

    nested = item.get("rating")
    if isinstance(nested, dict):
        if rating is None:
            rating = _number(nested.get("averageRating"), float)
        if reviews is None:
            reviews = _number(nested.get("numberOfReviews"), int)

    # A zero rating is Walmart's "not rated yet", not a one-star product.
    return (rating or None), reviews


def _to_product(item: dict) -> Optional[Product]:
    # A paid placement is not a demand signal; excluded exactly as on the
    # Amazon path, so a sponsored row can never enter a best-seller ranking.
    if item.get("isSponsoredFlag") or item.get("sponsoredProduct"):
        return None

    name = (item.get("name") or item.get("displayName") or "").strip()
    path = item.get("canonicalUrl") or ""
    if not name or not path:
        return None
    url = path if path.startswith("http") else f"{BASE_URL}{path}"

    price_text, price_min = _price(item)
    rating, review_count = _rating_fields(item)
    image = item.get("image") or item.get("imageInfo")
    if isinstance(image, dict):
        image = image.get("thumbnailUrl") or image.get("url")

    return Product(
        site="walmart",
        title=name,
        product_url=url,
        image_url=best_image(image if isinstance(image, str) else None, site="walmart"),
        price_text=price_text,
        price_min=price_min,
        price_max=price_min,
        currency="USD" if price_min is not None else None,
        seller_name=item.get("sellerName") or "Walmart",
        rating=rating,
        review_count=review_count,
        identifier=str(item["usItemId"]) if item.get("usItemId") else None,
    )


# Every Walmart product URL ends in its numeric item id: /ip/Some-Name/15334974461
ITEM_ID_RE = re.compile(r"/ip/(?:[^/]*/)?(\d{6,})")


def item_id_from_url(url: str) -> Optional[str]:
    match = ITEM_ID_RE.search(url or "")
    return match.group(1) if match else None


PRICE_IN_TEXT_RE = re.compile(r"\$([\d,]+\.\d{2})")


def prices_from_dom(html: str) -> dict[str, float]:
    """Prices as actually painted on the rendered search page, keyed by item id.

    Walmart zeroes every price in `__NEXT_DATA__` and fills the grid in client
    side, so the *rendered* page is the only place the blob's own products carry
    a price. That matters because the alternative source — a second productList
    request — returns a materially different set of products: measured on one
    live search, productList priced 21 of the blob's 40 rows and the rendered
    DOM priced 22, but they were not the same rows, and together they covered
    28. So this is used alongside productList, not instead of it.

    $0.00 is skipped: it is Walmart's placeholder for "price hidden until you
    pick a store", and a zero price sails through every has-a-price check and
    then drags the Market Snapshot median and the margin calculator to nothing.
    """
    from parsel import Selector

    prices: dict[str, float] = {}
    for tile in Selector(text=html).css("[data-item-id]"):
        item_id = None
        for href in tile.css("a::attr(href)").getall():
            item_id = item_id_from_url(href)
            if item_id:
                break
        if not item_id or item_id in prices:
            continue
        text = " ".join(tile.css("::text").getall())
        for match in PRICE_IN_TEXT_RE.finditer(text):
            value = float(match.group(1).replace(",", ""))
            if value > 0:
                prices[item_id] = value
                break
    return prices


def parse_search_results(html: str) -> list[Product]:
    """Pure function over page HTML — testable against a saved fixture, like the
    other parsers. Returns [] when the blob is missing so callers can fall back
    to Zyte's generic extraction rather than failing the site."""
    match = NEXT_DATA_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    items = _find_items(data)
    products = [p for p in (_to_product(i) for i in items) if p]

    # Walmart repeats a product once per variant swatch; the first occurrence
    # is the one the grid actually renders.
    seen: set[str] = set()
    unique: list[Product] = []
    for p in products:
        key = p.identifier or p.product_url
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique
