"""Group image-search results by whether their thumbnail actually looks like
the query photo, using perceptual hashing (imagehash.phash) rather than
cross-site title text — titles rarely match verbatim between Alibaba,
AliExpress, Made-in-China, and Google Lens, but the product photo is the one
signal that's directly comparable across all of them.

Zyte's API has no image-similarity product of its own (only structured-data
extraction), so this comparison runs locally: fetch each result's thumbnail,
hash it, and compare hamming distance against the query image's hash.
"""
import asyncio
from io import BytesIO

import httpx
import imagehash
from PIL import Image

from .models import Product, Seller

HASH_BITS = 64  # imagehash.phash default (8x8 DCT hash)
MATCH_DISTANCE_THRESHOLD = 12  # hamming distance; lower = same product, not just similar
FETCH_CONCURRENCY = 8
FETCH_TIMEOUT_SECONDS = 6.0
# Supplier-site CDNs (alicdn, made-in-china's image hosts, etc.) commonly 403
# httpx's default "python-httpx/x.x" UA as a bot signature — a normal browser
# UA gets treated the same as any other hotlinking browser tab.
FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


def _phash(image_bytes: bytes) -> imagehash.ImageHash:
    with Image.open(BytesIO(image_bytes)) as img:
        return imagehash.phash(img.convert("RGB"))


async def _fetch_image_bytes(client: httpx.AsyncClient, url: str) -> bytes | None:
    try:
        response = await client.get(
            url, timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True, headers=FETCH_HEADERS
        )
        response.raise_for_status()
        return response.content
    except httpx.HTTPError:
        return None


async def _score_product(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    query_hash: imagehash.ImageHash,
    product: Product,
) -> tuple[Product, float | None]:
    if not product.image_url:
        return product, None
    async with semaphore:
        image_bytes = await _fetch_image_bytes(client, product.image_url)
    if not image_bytes:
        return product, None
    try:
        product_hash = await asyncio.to_thread(_phash, image_bytes)
    except Exception:
        return product, None
    distance = query_hash - product_hash
    return product, max(0.0, 1 - distance / HASH_BITS)


async def score_against_query(
    products: list[Product], query_image_bytes: bytes
) -> list[tuple[Product, float | None]]:
    """Score each product's thumbnail against the query photo without filtering
    or collapsing anything.

    match_and_group() answers "which of these is *the* product" and merges the
    winners into one card. The sourcing pipeline needs the opposite: every
    candidate kept, each carrying its own similarity so it can be tiered
    (identical / exact / similar) and ranked. Same hashing, different contract.

    A None score means the thumbnail couldn't be fetched or hashed — distinct
    from a low score, and callers must not treat the two alike.
    """
    if not products:
        return []
    try:
        query_hash = await asyncio.to_thread(_phash, query_image_bytes)
    except Exception:
        return [(p, None) for p in products]

    semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        return list(
            await asyncio.gather(
                *(_score_product(client, semaphore, query_hash, p) for p in products)
            )
        )


def _as_seller(p: Product) -> Seller:
    return Seller(
        site=p.site,
        seller_name=p.seller_name,
        seller_url=p.seller_url,
        product_url=p.product_url,
        price_text=p.price_text,
        price_min=p.price_min,
        price_max=p.price_max,
        currency=p.currency,
        moq=p.moq,
        contact_type=p.contact_type,
        contact_value=p.contact_value,
    )


async def match_and_group(products: list[Product], query_image_bytes: bytes) -> tuple[list[Product], list[str]]:
    """Score every result's thumbnail against the query photo, keep only
    visual matches, and merge them into one card (sellers = every matching
    listing) — the "same product, different supplier" view."""
    if not products:
        return [], []

    try:
        query_hash = await asyncio.to_thread(_phash, query_image_bytes)
    except Exception:
        return products, ["Could not analyze the search photo for visual matching — showing unfiltered results."]

    semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)
    async with httpx.AsyncClient() as client:
        scored = await asyncio.gather(*(_score_product(client, semaphore, query_hash, p) for p in products))

    matched: list[tuple[Product, float]] = []
    unscored: list[Product] = []
    rejected = 0
    for product, similarity in scored:
        if similarity is None:
            unscored.append(product)
        elif (1 - similarity) * HASH_BITS <= MATCH_DISTANCE_THRESHOLD:
            matched.append((product, similarity))
        else:
            rejected += 1

    warnings: list[str] = []
    if rejected:
        warnings.append(f"Filtered out {rejected} result(s) whose photo didn't visually match your search image.")
    if unscored:
        warnings.append(
            f"{len(unscored)} result(s) could not be visually verified (thumbnail failed to load) — kept, unranked."
        )

    if not matched:
        for product in unscored:
            product.image_match = None
        warnings.insert(0, "No confident visual matches found — showing unverified results instead.")
        return unscored, warnings

    matched.sort(key=lambda pair: pair[1], reverse=True)
    primary, best_similarity = matched[0]
    primary.image_match = round(best_similarity, 3)
    primary.sellers = [_as_seller(p) for p, _ in matched]

    for product in unscored:
        product.image_match = None

    return [primary, *unscored], warnings
