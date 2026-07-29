"""Temu and Costco via Apify actors.

Both sites defeated every other transport we tried, and the failures were
measured, not assumed:

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

Uses the same APIFY_TOKEN as Pinterest and Google Lens.
"""
import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .config import settings
from .models import Product
from .product_images import best_image

APIFY_RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
TIMEOUT_SECONDS = 300.0
# Costco's actor rejects anything below 30; Temu's is uncapped.
DEFAULT_MAX_ITEMS = 40


class ApifyRetailError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.apify_token)


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
                params={"token": settings.apify_token, "timeout": int(TIMEOUT_SECONDS)},
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

    products = [p for p in (config.to_product(i) for i in items if isinstance(i, dict)) if p]
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
