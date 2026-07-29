"""Reverse image search for trending items via Apify's Google Lens actor
(`borderline~google-lens`), run in "products" and "exact-match" modes:
- products: AI-identified shopping matches (title/price/vendor guesses).
- exact-match: pages hosting the pixel-identical image — the strongest
  signal that a listing is *this* item, not just something similar.
Together these widen trending sourcing beyond the three configured
marketplace scrapers to whatever Google Lens finds across the web.

The actor's dataset items are keyed by search type, e.g.
{"products": {"results": [...]}} or {"exact-match": {"results": [...]}} —
a type with no matches for a given image simply has no item pushed for it.

Runs are started async and polled rather than using Apify's
run-sync-get-dataset-items endpoint: this actor routinely takes 2-3 minutes,
and Apify's own docs warn that endpoint's long-held HTTP connection "might
break" — which in practice showed up as either a dropped connection or an
empty dataset read back before the run had actually finished. Polling with
short-lived requests avoids holding any single connection open that long.
"""
import asyncio
import base64

import httpx

from .config import settings
from .models import Product

APIFY_BASE = "https://api.apify.com/v2"
SEARCH_TYPES = ["products", "exact-match"]
EXACT_MATCH_LIMIT = 15
POLL_INTERVAL_SECONDS = 5
MAX_POLL_SECONDS = 280
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}


class GoogleLensError(Exception):
    pass


def _product_from_result(result: dict, site: str) -> Product | None:
    title = result.get("title")
    link = result.get("link") or result.get("href")
    image_url = result.get("thumbnail") or result.get("image") or result.get("imageUrl")
    # A card with no picture reads as broken in the UI, so results the actor
    # didn't attach an image to are dropped rather than shown with a placeholder.
    if not title or not link or not image_url:
        return None
    return Product(
        site=site,
        title=title,
        image_url=image_url,
        price_text=result.get("price") or None,
        seller_name=result.get("vendor") or result.get("source"),
        product_url=link,
    )


def _parse_dataset_items(items: list[dict]) -> list[Product]:
    products: list[Product] = []
    exact_match_count = 0
    for dataset_item in items:
        for result in (dataset_item.get("products") or {}).get("results", []):
            product = _product_from_result(result, site="google_lens")
            if product:
                products.append(product)

        if exact_match_count >= EXACT_MATCH_LIMIT:
            continue
        for result in (dataset_item.get("exact-match") or {}).get("results", []):
            if exact_match_count >= EXACT_MATCH_LIMIT:
                break
            product = _product_from_result(result, site="google_lens_exact")
            if product:
                products.append(product)
                exact_match_count += 1

    return products


async def _start_run(client: httpx.AsyncClient, data_uri: str) -> str:
    response = await client.post(
        f"{APIFY_BASE}/acts/{settings.google_lens_actor}/runs",
        params={"token": settings.apify_token},
        json={"searchTypes": SEARCH_TYPES, "imagesBase64": [data_uri]},
        headers={"Content-Type": "application/json"},
    )
    if not (200 <= response.status_code < 300):
        raise GoogleLensError(f"Google Lens run failed to start ({response.status_code}): {response.text[:300]}")
    return response.json()["data"]["id"]


async def _wait_for_run(client: httpx.AsyncClient, run_id: str) -> str:
    elapsed = 0
    status = "READY"
    while elapsed < MAX_POLL_SECONDS:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
        try:
            response = await client.get(
                f"{APIFY_BASE}/actor-runs/{run_id}", params={"token": settings.apify_token}
            )
            response.raise_for_status()
        except httpx.HTTPError:
            continue  # transient poll failure — try again next interval
        status = response.json()["data"]["status"]
        if status in TERMINAL_STATUSES:
            return status
    return status


async def _fetch_dataset_items(client: httpx.AsyncClient, run_id: str) -> list[dict]:
    response = await client.get(
        f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items", params={"token": settings.apify_token}
    )
    if not (200 <= response.status_code < 300):
        raise GoogleLensError(f"Could not fetch Google Lens results ({response.status_code}): {response.text[:300]}")
    return response.json()


async def _run_lens_query(image_bytes: bytes, content_type: str) -> list[Product]:
    data_uri = f"data:{content_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        run_id = await _start_run(client, data_uri)
        status = await _wait_for_run(client, run_id)
        if status != "SUCCEEDED":
            raise GoogleLensError(f"Google Lens run did not succeed (status={status})")
        items = await _fetch_dataset_items(client, run_id)

    return _parse_dataset_items(items)


async def search_google_lens_products(image_bytes: bytes, content_type: str) -> tuple[list[Product], list[str]]:
    """Returns (products, warnings). Google Lens can genuinely find nothing for a
    given crop — one empty-result retry is attempted before surfacing a warning
    instead of silently returning nothing."""
    if not settings.apify_token:
        raise GoogleLensError("APIFY_TOKEN is not configured — set it in backend/.env")

    try:
        products = await _run_lens_query(image_bytes, content_type)
    except httpx.HTTPError as e:
        raise GoogleLensError(f"Google Lens request failed: {e}") from e
    if products:
        return products, []

    try:
        products = await _run_lens_query(image_bytes, content_type)
    except httpx.HTTPError as e:
        raise GoogleLensError(f"Google Lens request failed: {e}") from e
    if products:
        return products, []

    return [], ["No Google Lens matches found for this image"]
