"""Google Lens picture search via SerpApi.

Replaces the Apify `borderline~google-lens` actor, which took 2-3 minutes per
search and had to be started-then-polled because its long-held connection kept
breaking. SerpApi answers the same query in ~4 seconds with a plain GET, and
exposes the exact/visual split as a first-class `type` parameter:

    type=exact_matches    pages hosting the pixel-identical image  -> 400 results
    type=visual_matches   things that merely look like it          ->  59 results

That distinction is the whole basis of the app's "Exact match" green cards vs
the amber "closest visual matches" fallback, so it maps straight onto the
existing `google_lens_exact` / `google_lens` site tags — the frontend contract
is unchanged.

**The upload problem.** SerpApi's Lens endpoint takes only a publicly reachable
image URL; there is no upload, no base64, no multipart. A user's photo lives on
their machine and ours, neither of which Google can fetch. So an uploaded photo
has to be published somewhere first.

The current host is uguu.se — anonymous, no credentials, files expire in a few
hours. It is the pragmatic default, not the right long-term answer: every
uploaded photo transits a third party we don't control. Set IMAGE_HOST_BASE (an
S3/R2 bucket) to take that dependency out of the loop; `_publish` is the single
seam to reimplement.
"""
import asyncio

import httpx

from . import credentials
from .models import Product

SERPAPI_URL = "https://serpapi.com/search"
UGUU_UPLOAD_URL = "https://uguu.se/upload"

# Both are fetched per search: exact matches answer "is this the same product",
# visual matches answer "what else looks like it".
SEARCH_TYPES = ("exact_matches", "visual_matches")
EXACT_MATCH_LIMIT = 25
VISUAL_MATCH_LIMIT = 40
TIMEOUT_SECONDS = 120.0
UPLOAD_TIMEOUT_SECONDS = 60.0

# uguu rejects requests without a browser-ish UA.
UPLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

SITE_FOR_TYPE = {
    "exact_matches": "google_lens_exact",
    "visual_matches": "google_lens",
}
LIMIT_FOR_TYPE = {
    "exact_matches": EXACT_MATCH_LIMIT,
    "visual_matches": VISUAL_MATCH_LIMIT,
}


class SerpLensError(Exception):
    pass


def is_configured() -> bool:
    return bool(credentials.SERPAPI)


async def _publish(image_bytes: bytes, content_type: str) -> str:
    """Make an uploaded photo reachable by Google, returning its public URL.

    The single seam to swap when moving off the anonymous host — everything
    else in this module only needs a URL.
    """
    ext = {"image/png": "png", "image/webp": "webp", "image/bmp": "bmp"}.get(content_type, "jpg")
    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        try:
            response = await client.post(
                UGUU_UPLOAD_URL,
                files={"files[]": (f"query.{ext}", image_bytes, content_type or "image/jpeg")},
                headers=UPLOAD_HEADERS,
            )
        except httpx.HTTPError as e:
            raise SerpLensError(f"Could not publish the photo for Lens ({type(e).__name__}).") from e

    if response.status_code != 200:
        raise SerpLensError(f"Image host returned {response.status_code}.")
    try:
        url = response.json()["files"][0]["url"]
    except (KeyError, IndexError, ValueError) as e:
        raise SerpLensError("Image host returned an unexpected response.") from e
    if not url.startswith("http"):
        raise SerpLensError("Image host returned no usable URL.")
    return url


def _to_product(match: dict, site: str) -> Product | None:
    title = (match.get("title") or "").strip()
    link = match.get("link")
    image = match.get("thumbnail") or match.get("image")
    # A card with no picture reads as broken in the UI, so matches without one
    # are dropped rather than shown with a placeholder — same rule the previous
    # Lens implementation used.
    if not title or not link or not image:
        return None

    price = match.get("price")
    if isinstance(price, dict):
        price_text = price.get("extracted_value") and price.get("value") or price.get("value")
    else:
        price_text = price

    return Product(
        site=site,
        title=title[:300],
        product_url=link,
        image_url=image,
        price_text=str(price_text) if price_text else None,
        seller_name=match.get("source") or None,
    )


async def _search_one(client: httpx.AsyncClient, image_url: str, search_type: str) -> tuple[list[Product], list[str]]:
    try:
        response = await client.get(
            SERPAPI_URL,
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

    payload = response.json()
    if payload.get("error"):
        # "hasn't returned any results" is an ordinary outcome, not a fault.
        message = str(payload["error"])
        if "hasn't returned any results" in message:
            return [], []
        return [], [f"Lens {search_type}: {message[:120]}"]

    site = SITE_FOR_TYPE[search_type]
    matches = payload.get(search_type) or payload.get("visual_matches") or []
    products = [p for p in (_to_product(m, site) for m in matches if isinstance(m, dict)) if p]
    return products[: LIMIT_FOR_TYPE[search_type]], []


async def search_by_url(image_url: str) -> tuple[list[Product], list[str]]:
    """Run both Lens searches against an already-public image URL."""
    if not is_configured():
        return [], ["Google Lens is not configured — set SERPAPI_KEY in backend/.env."]

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        results = await asyncio.gather(
            *(_search_one(client, image_url, t) for t in SEARCH_TYPES)
        )

    products: list[Product] = []
    warnings: list[str] = []
    for found, warned in results:
        products.extend(found)
        warnings.extend(warned)

    if not products and not warnings:
        warnings.append("No Google Lens matches found for this image.")
    return products, warnings


async def search(image_bytes: bytes, content_type: str) -> tuple[list[Product], list[str]]:
    """Publish an uploaded photo, then Lens-search it."""
    if not is_configured():
        return [], ["Google Lens is not configured — set SERPAPI_KEY in backend/.env."]
    image_url = await _publish(image_bytes, content_type or "image/jpeg")
    return await search_by_url(image_url)
