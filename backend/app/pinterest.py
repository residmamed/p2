"""Idea -> Pinterest inspiration images, via Apify actor
`fetch_cat~pinterest-search-scraper`. `run-sync-get-dataset-items` blocks
until the actor run finishes, so this is a plain awaited request.
"""
import httpx

from . import credentials
from .config import settings
from .models import InspirationImage

APIFY_URL_TEMPLATE = "https://api.apify.com/v2/actors/{actor}/run-sync-get-dataset-items?token={token}"


class PinterestError(Exception):
    pass


def _pick_image(item: dict) -> str:
    images = item.get("images") or {}
    orig = images.get("orig") if isinstance(images, dict) else None
    first_image = next(iter(images.values()), None) if isinstance(images, dict) and images else None
    return (
        item.get("imageUrl")
        or item.get("image")
        or item.get("imageUrlOriginal")
        or (orig or {}).get("url")
        or (first_image or {}).get("url")
        or item.get("thumbnail")
        or (item.get("media") or {}).get("url")
        or ""
    )


def _pick_title(item: dict) -> str | None:
    return item.get("title") or item.get("description") or item.get("grid_title") or item.get("alt")


def _pick_link(item: dict) -> str | None:
    return item.get("link") or item.get("url") or item.get("pinUrl")


async def search_pinterest(idea: str, n: int = 20) -> list[InspirationImage]:
    if not credentials.APIFY:
        raise PinterestError("APIFY_TOKEN is not configured — set it in backend/.env")

    url = APIFY_URL_TEMPLATE.format(
        actor=settings.pinterest_actor, token=credentials.APIFY.next()
    )
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            url,
            json={"queries": [idea], "maxResultsPerQuery": n},
            headers={"Content-Type": "application/json"},
        )
    if not (200 <= response.status_code < 300):
        raise PinterestError(f"Pinterest search failed ({response.status_code}): {response.text[:300]}")

    items = response.json()
    results = []
    for item in items:
        image_url = _pick_image(item)
        if not image_url:
            continue
        results.append(
            InspirationImage(image_url=image_url, pin_url=_pick_link(item), title=_pick_title(item))
        )
    return results
