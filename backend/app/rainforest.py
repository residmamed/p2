"""Amazon via Rainforest API instead of generic scraping.

Amazon is the one site where a paid structured API decisively beats Zyte's
generic productList, and the live probes show exactly why:

    Zyte productList   name, price, image, url            (no rating, no reviews)
    Rainforest         + rating, ratings_total, asin,
                       recent_sales ("10K+ bought in past month"), amazons_choice

`recent_sales` is the strongest demand signal available anywhere in this app —
actual recent purchase volume straight from Amazon, not a rank someone inferred.
It's what Product Search's whole premise wants and no other source publishes.

`asin` matters too: it's a real Shared Identifier, so Amazon rows can finally
merge with other sites' listings under the identifier-only merge rule instead of
never merging at all.

Chosen over SerpApi's amazon engine, which returns the same fields with the same
best-seller ordering, for one reason: **exact review counts**. SerpApi rounds to
three significant figures — 131,434 arrives as 131,400 and 53,997 as 53,900 —
where Rainforest reports the real number. It is also the cheaper of the two per
request. SerpApi remains the fallback (app/serpapi_retail.py), and Zyte the
fallback behind that.

Results are requested in Amazon's own best-selling order (`sort_by`), so Site
Rank is a real ranking rather than relevance order. Verified live: the sorted
list leads with Owala and Stanley, which are in fact the category's best
sellers.
"""
import asyncio

import httpx

from .config import settings
from .models import Product
from .product_images import best_image
from .retail_browser import _parse_sold

RAINFOREST_URL = "https://api.rainforestapi.com/request"
# Amazon's own best-selling order. Without it the API returns relevance order,
# which would make Site Rank a claim the data doesn't support.
BESTSELLER_SORT = "bestseller_rankings"
TIMEOUT_SECONDS = 90.0
MAX_RETRIES = 2
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RainforestError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.rainforest_api_key)


def _price(item: dict) -> tuple[str | None, float | None, str | None]:
    price = item.get("price") or {}
    if not isinstance(price, dict):
        return None, None, None
    value = price.get("value")
    try:
        value = float(value) if value is not None else None
    except (TypeError, ValueError):
        value = None
    return price.get("raw"), value, price.get("currency")


def to_product(item: dict) -> Product | None:
    """Map one Rainforest search result onto our Product model.

    Sponsored rows are excluded the same way the Zyte path excludes them: a paid
    placement is not a demand signal, and letting one into a best-seller ranking
    is the exact failure this app is built to avoid.
    """
    title = (item.get("title") or "").strip()
    url = item.get("link")
    if not title or not url:
        return None
    if item.get("sponsored") or item.get("is_sponsored"):
        return None

    price_text, price_min, currency = _price(item)

    rating = item.get("rating")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None

    reviews = item.get("ratings_total")
    try:
        reviews = int(reviews) if reviews is not None else None
    except (TypeError, ValueError):
        reviews = None

    return Product(
        site="amazon",
        title=title,
        product_url=url,
        image_url=best_image(item.get("image"), site="amazon"),
        price_text=price_text,
        price_min=price_min,
        price_max=price_min,
        currency=currency,
        seller_name="Amazon",
        rating=rating,
        review_count=reviews,
        # "10K+ bought in past month" -> 10000. Shares the parser with Temu's
        # per-card sold counts, since both mean the same thing.
        popularity_score=_parse_sold(item.get("recent_sales") or ""),
        identifier=item.get("asin") or None,
    )


async def search(query: str, *, domain: str = "amazon.com") -> tuple[list[Product], list[str]]:
    """Fetch Amazon search results. Returns (products, warnings)."""
    if not is_configured():
        return [], ["[Amazon] Rainforest not configured — set RAINFOREST_API_KEY."]

    params = {
        "api_key": settings.rainforest_api_key,
        "type": "search",
        "amazon_domain": domain,
        "search_term": query,
        "sort_by": BESTSELLER_SORT,
    }

    attempt = 0
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        while True:
            attempt += 1
            try:
                response = await client.get(RAINFOREST_URL, params=params)
            except httpx.HTTPError as e:
                if attempt <= MAX_RETRIES:
                    await asyncio.sleep(min(2**attempt, 8))
                    continue
                raise RainforestError(f"Rainforest request failed ({type(e).__name__})") from e

            if response.status_code == 200:
                break
            if response.status_code in RETRYABLE_STATUS and attempt <= MAX_RETRIES:
                await asyncio.sleep(min(2**attempt, 8))
                continue
            raise RainforestError(
                f"Rainforest request failed ({response.status_code}): {response.text[:200]}"
            )

    payload = response.json()
    items = payload.get("search_results") or []
    products = [p for p in (to_product(i) for i in items) if p]

    warnings: list[str] = []
    with_sales = sum(1 for p in products if p.popularity_score is not None)
    if products and not with_sales:
        warnings.append(
            "[Amazon] No 'bought in past month' figures on these results — "
            "ranked by rating x reviews instead."
        )
    return products, warnings
