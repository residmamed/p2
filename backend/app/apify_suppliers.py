"""Alibaba and 1688 supplier listings from a product photo, via Apify actors.

This replaces the Browserbase upload dance (app/scrapers/image_discovery.py)
for these two sites. That path drives each site's own photo-upload widget in a
cloud browser and then parses the results page; on Alibaba it is bot-checked
essentially every time, which is why CONTEXT.md's Supplier Profile entry records
"Captcha Interception" arriving as a company name. These actors run the same
reverse image search server-side and hand back structured rows.

Two different actors, because they were measured rather than assumed. On the
same two live product photos:

    dev00/...reverse-image-search-api   alibaba -> 100 rows.  1688 -> 0 rows,
                                        twice, on a run that reported success.
    devcake/scraper-by-image            1688    -> 40 rows with shop name, MOQ,
                                        price, ratings and sold counts.

So Alibaba goes through dev00 and 1688 through devcake. Both are keyed off the
product's image URL, which is exactly what the Manufacturer Search already has
in hand (`/api/sourcing/by-url`).

What this buys beyond reaching the sites at all: both actors name the seller and
link its company page on every row. Supplier Resolution (app/supplier_resolve.py)
exists because search-results cards name the product and not the company — it
skips any listing that already has a seller, so these rows cost zero extra Zyte
calls.

Uses the same APIFY_TOKEN as Pinterest, Google Lens and the Temu/Costco actors.
"""
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .config import settings
from .models import Product

APIFY_RUN_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
TIMEOUT_SECONDS = 300.0

# Every listing kept here is an image the phash stage then downloads and the
# vision agent may re-encode into a request body, so the actor's 100 rows are
# not free to carry. Truncation is reported rather than silent — see search().
MAX_LISTINGS = 40


def is_configured() -> bool:
    return bool(settings.apify_token)


def _num(value: Any, cast: Callable):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _price_pair(low: Any, high: Any) -> tuple[float | None, float | None]:
    """Min/max, ordered. devcake ships them reversed on some rows — measured:
    `price_min: 9.4, price_max: 8.5` on a live 1688 result — and a max below the
    min would make the range read backwards on the card."""
    a, b = _num(low, float), _num(high, float)
    values = [v for v in (a, b) if v]
    if not values:
        return None, None
    return min(values), max(values)


def _price_text(low: float | None, high: float | None, symbol: str) -> str | None:
    if low is None:
        return None
    if high is not None and high > low:
        return f"{symbol}{low:,.2f} - {symbol}{high:,.2f}"
    return f"{symbol}{low:,.2f}"


# --- Alibaba (dev00) -------------------------------------------------------

def _alibaba_product(item: dict) -> Product | None:
    title = (item.get("title") or "").strip()
    url = item.get("productUrl") or ""
    if not title or not url:
        return None

    price = _num(item.get("price"), float) or None
    currency = item.get("currency") or "USD"
    symbol = "$" if currency == "USD" else f"{currency} "
    moq = item.get("minOrderQty")

    return Product(
        site="alibaba",
        title=title[:300],
        product_url=url,
        image_url=item.get("imageUrl") or None,
        price_text=_price_text(price, None, symbol),
        price_min=price,
        price_max=price,
        currency=currency if price is not None else None,
        # Alibaba states MOQ as a bare count; the unit is pieces on this feed.
        moq=f"MOQ {moq}" if moq not in (None, "") else None,
        seller_name=(item.get("supplierName") or "").strip() or None,
        seller_url=item.get("supplierUrl") or None,
        rating=_num(item.get("reviewScore"), float),
    )


# --- 1688 (devcake) --------------------------------------------------------

def _1688_product(item: dict) -> Product | None:
    title = (item.get("title") or item.get("original_title") or "").strip()
    url = item.get("product_url") or ""
    if not title or not url:
        return None

    low, high = _price_pair(item.get("price_min"), item.get("price_max"))
    # 1688 quotes in yuan and this feed says so on every row. Left in CNY rather
    # than converted: there is no rate in this codebase to convert with, and an
    # invented one would put a wrong dollar figure on a supplier quote. The
    # frontend's own parsePrice already reads a ¥ price for its metrics.
    currency = item.get("currency") or item.get("currency_code") or "CNY"
    symbol = "¥" if currency == "CNY" else f"{currency} "
    moq = item.get("moq")
    unit = (item.get("unit") or "").strip()

    return Product(
        site="1688",
        title=title[:300],
        product_url=url,
        image_url=item.get("image_url") or None,
        price_text=_price_text(low, high, symbol),
        price_min=low,
        price_max=high,
        currency=currency if low is not None else None,
        moq=f"MOQ {moq}{unit}" if moq not in (None, "") else None,
        seller_name=(item.get("shop_name") or "").strip() or None,
        seller_url=item.get("shop_url") or None,
        # Ratings arrive at full float precision (3.2334712); the UI renders one
        # decimal, and the extra digits are not accuracy.
        rating=round(r, 2) if (r := _num(item.get("rating"), float)) else None,
        review_count=_num(item.get("review_count"), int),
        # Units moved, the same demand signal Temu's sales_num carries.
        popularity_score=_num(item.get("sold_count"), float),
    )


@dataclass(frozen=True)
class ActorConfig:
    site: str
    label: str
    actor: str
    build_input: Callable[[str], dict]
    to_product: Callable[[dict], Product | None]


ACTORS: dict[str, ActorConfig] = {
    "alibaba": ActorConfig(
        site="alibaba",
        label="Alibaba",
        actor="dev00~alibaba-1688-aliexpress-reverse-image-search-api",
        build_input=lambda image_url: {
            "imageUrl": image_url,
            "destination": "alibaba",
            "language": "en",
            "currency": "USD",
        },
        to_product=_alibaba_product,
    ),
    "1688": ActorConfig(
        site="1688",
        label="1688",
        actor="devcake~scraper-by-image",
        build_input=lambda image_url: {
            "provider": "1688",
            "imageUrls": [image_url],
            "maxProducts": MAX_LISTINGS,
        },
        to_product=_1688_product,
    ),
}

SUPPORTED_SITES = tuple(ACTORS)


def handles(site: str) -> bool:
    return site in ACTORS and is_configured()


async def search(site: str, image_url: str) -> tuple[list[Product], list[str]]:
    """Reverse-image-search one site. Never raises — a dead actor is a warning
    and an empty site, exactly like every other source in this pipeline."""
    config = ACTORS.get(site)
    if config is None:
        return [], [f"No Apify actor configured for supplier site {site!r}"]
    if not is_configured():
        return [], [f"[{config.label}] Apify not configured — set APIFY_TOKEN."]

    url = APIFY_RUN_URL.format(actor=config.actor)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                params={"token": settings.apify_token, "timeout": int(TIMEOUT_SECONDS) - 20},
                json=config.build_input(image_url),
            )
    except httpx.HTTPError as e:
        return [], [f"[{config.label}] Apify request failed ({type(e).__name__})."]

    if response.status_code not in (200, 201):
        # The actor's own failures arrive as a 400 with a run id in the body;
        # the id is worth keeping, it's how the run log is found.
        return [], [f"[{config.label}] Apify returned {response.status_code}: {response.text[:180]}"]

    try:
        items = response.json()
    except ValueError:
        return [], [f"[{config.label}] Apify returned a non-JSON body."]
    if not isinstance(items, list):
        return [], [f"[{config.label}] Apify returned no dataset items."]

    products = [p for p in (config.to_product(i) for i in items if isinstance(i, dict)) if p]
    if not products:
        return [], [
            f"[{config.label}] The reverse image search ran but matched nothing for this photo."
        ]

    warnings: list[str] = []
    if len(products) > MAX_LISTINGS:
        warnings.append(
            f"[{config.label}] Kept the {MAX_LISTINGS} closest of {len(products)} listings "
            "returned; the rest were not visually checked."
        )
        products = products[:MAX_LISTINGS]
    return products, warnings
