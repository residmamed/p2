"""Amazon and Walmart via SerpApi's dedicated engines.

Both sites are fetched from SerpApi rather than scraped, and both are asked for
the site's *own* best-selling order — `s=exact-aware-popularity-rank` on Amazon,
`sort=best_seller` on Walmart — so Site Rank is a real ranking rather than page
order. Verified live: the sorted Amazon list leads with Owala and Stanley, which
are in fact the category's best sellers, and is materially different from the
unsorted one.

Why this replaced the previous Walmart path (Zyte productList + a second fetch
to scrape `__NEXT_DATA__` for stars), measured on two live queries:

    ratings in the results       Zyte  0 of 41        SerpApi  40 of 40
    requests per search          Zyte  2              SerpApi  1
    prices wrong vs the page     Zyte  13% and 26%    SerpApi  0

Zyte's Walmart prices failed two ways, both confirmed against the product pages.
It reported the struck-through pre-Rollback price as the current one (VEAT00L
earbuds: $159.98 against an actual $20.49), and it truncated the cents off
three-digit prices — "249.99" arriving as "249.0" — which the cents heuristic
then read as a cents figure and divided, listing a $249.99 item at $2.49. A
typed `offer_price`, separate from `was_price`, removes that whole class of
error rather than tuning the threshold again.

Amazon keeps Rainforest as a fallback (app/rainforest.py) for when SerpApi is
unconfigured or fails; Walmart falls back to the Zyte path in bestsellers.py.
One accepted regression on the Amazon swap: SerpApi rounds review counts to
three significant figures (131,434 arrives as 131,400) where Rainforest is
exact. It moves the log-scaled Opportunity Score by nothing measurable, but the
number on the card is approximate.
"""
import asyncio

import httpx

from . import credentials
from .models import Product
from .product_images import best_image
from .retail_browser import _parse_sold

SERPAPI_URL = "https://serpapi.com/search"
TIMEOUT_SECONDS = 120.0
MAX_RETRIES = 2
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# The best-selling sort each engine exposes. Amazon's value is the same one the
# site puts in its own URL; Walmart's is SerpApi's alias for it.
ENGINE_PARAMS = {
    "amazon": {"engine": "amazon", "amazon_domain": "amazon.com", "s": "exact-aware-popularity-rank"},
    "walmart": {"engine": "walmart", "sort": "best_seller"},
}
# Each engine names the search term differently.
QUERY_PARAM = {"amazon": "k", "walmart": "query"}

SUPPORTED_SITES = tuple(ENGINE_PARAMS)


class SerpApiError(Exception):
    pass


def is_configured() -> bool:
    return bool(credentials.SERPAPI)


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price_value(value) -> float | None:
    """A price, with zero read as "not published" rather than "free".

    SerpApi ships `primary_offer: {offer_price: 0, min_price: 0}` for listings
    Walmart won't price in search results — out of stock, or price shown only in
    the cart. Measured on one live `laptop` search: 1 of 40 rows. Zero is the
    worst possible stand-in for absent, because it passes every has-a-price
    check and then lands on the card as "$0.00" and drags the Market Snapshot
    median and the margin calculator down with it.
    """
    number = _to_float(value)
    return number if number else None


def _to_int(value) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _price_text(value: float | None, currency: str | None) -> str | None:
    if value is None:
        return None
    symbol = "$" if currency in (None, "", "USD") else currency
    sep = "" if not symbol[-1].isalnum() else " "
    return f"{symbol}{sep}{value:.2f}"


def _amazon_product(item: dict) -> Product | None:
    """Map one SerpApi Amazon organic result onto our Product model."""
    title = (item.get("title") or "").strip()
    url = item.get("link_clean") or item.get("link")
    if not title or not url:
        return None
    # A paid placement is not a demand signal — same exclusion the other paths
    # make, and the reason a best-seller ranking can be trusted at all.
    if item.get("sponsored"):
        return None

    price_min = _price_value(item.get("extracted_price"))
    price_raw = item.get("price") if price_min is not None else None

    return Product(
        site="amazon",
        title=title,
        product_url=url,
        image_url=best_image(item.get("thumbnail"), site="amazon"),
        price_text=price_raw if isinstance(price_raw, str) else _price_text(price_min, "USD"),
        price_min=price_min,
        price_max=price_min,
        currency="USD",
        seller_name="Amazon",
        rating=_to_float(item.get("rating")),
        review_count=_to_int(item.get("reviews")),
        # "20K+ bought in past month" -> 20000. The strongest demand signal in
        # the app: measured purchase volume, not an inferred position.
        popularity_score=_parse_sold(item.get("bought_last_month") or ""),
        identifier=item.get("asin") or None,
    )


def _walmart_product(item: dict) -> Product | None:
    """Map one SerpApi Walmart organic result onto our Product model."""
    title = (item.get("title") or "").strip()
    url = item.get("product_page_url")
    if not title or not url or item.get("sponsored"):
        return None

    # primary_offer is the buy-box: offer_price is what you pay today, was_price
    # the struck-through one. Reading the wrong field is exactly what made the
    # Zyte path overstate sale items, so only offer_price is ever used.
    offer = item.get("primary_offer") or {}
    price_min = _price_value(offer.get("offer_price")) or _price_value(offer.get("min_price"))
    currency = offer.get("currency") or "USD"

    return Product(
        site="walmart",
        title=title,
        product_url=url,
        image_url=best_image(item.get("thumbnail"), site="walmart"),
        price_text=_price_text(price_min, currency),
        price_min=price_min,
        price_max=price_min,
        currency=currency,
        # Walmart lists third-party sellers alongside its own stock; naming the
        # actual seller matters when the next step is sourcing the product.
        seller_name=item.get("seller_name") or "Walmart",
        rating=_to_float(item.get("rating")),
        review_count=_to_int(item.get("reviews")),
        # A real Shared Identifier, so Walmart rows can finally merge with other
        # sites' listings instead of never merging at all.
        identifier=item.get("us_item_id") or None,
    )


MAPPERS = {"amazon": _amazon_product, "walmart": _walmart_product}


async def _request(params: dict) -> dict:
    attempt = 0
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        while True:
            attempt += 1
            try:
                response = await client.get(SERPAPI_URL, params=params)
            except httpx.HTTPError as e:
                if attempt <= MAX_RETRIES:
                    await asyncio.sleep(min(2**attempt, 8))
                    continue
                raise SerpApiError(f"SerpApi request failed ({type(e).__name__})") from e

            if response.status_code == 200:
                return response.json()
            if response.status_code in RETRYABLE_STATUS and attempt <= MAX_RETRIES:
                await asyncio.sleep(min(2**attempt, 8))
                continue
            raise SerpApiError(
                f"SerpApi request failed ({response.status_code}): {response.text[:200]}"
            )


async def search(site_id: str, query: str, page: int = 1) -> tuple[list[Product], list[str]]:
    """Fetch one site's best-selling results. Returns (products, warnings).

    `page` walks further down the same best-selling sort, for the per-store
    "find more" button. Both engines count pages from 1, and each page is a
    separate billed search — so it is only ever requested on demand.
    """
    if site_id not in ENGINE_PARAMS:
        raise SerpApiError(f"No SerpApi engine configured for '{site_id}'.")
    label = site_id.capitalize()
    if not is_configured():
        return [], [f"[{label}] SerpApi not configured — set SERPAPI_KEY."]

    params = {
        **ENGINE_PARAMS[site_id],
        QUERY_PARAM[site_id]: query,
        "api_key": credentials.SERPAPI.next(),
    }
    if page > 1:
        params["page"] = str(page)
    payload = await _request(params)

    if payload.get("error"):
        message = str(payload["error"])
        # "hasn't returned any results" is an ordinary outcome, not a fault.
        if "hasn't returned any results" in message:
            return [], []
        raise SerpApiError(message[:200])

    mapper = MAPPERS[site_id]
    items = payload.get("organic_results") or []
    products = [p for p in (mapper(i) for i in items if isinstance(i, dict)) if p]

    warnings: list[str] = []
    if not products:
        # Silence here used to make the fallback invisible: the caller would
        # quietly drop to the Zyte path and the user would see only that path's
        # complaints, with nothing to say why the good source wasn't used.
        warnings.append(
            f"[{label}] SerpApi returned no listings for this search; "
            "falling back to generic extraction."
        )
        return products, warnings
    priced = sum(1 for p in products if p.price_min is not None)
    if products and priced < len(products):
        warnings.append(
            f"[{label}] {len(products) - priced} of {len(products)} listings publish no "
            "price on the results page — shown without one rather than guessed."
        )
    return products, warnings
