"""Trending: Idea -> Pinterest inspiration images -> detect items in a
picked image -> crop each -> (client then searches a crop the same way it
searches an uploaded photo, via /api/search/image).
"""
import asyncio
import time
import uuid

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from . import detection, product_page_enrich, serp_lens
from .crop import crop_and_encode
from .google_lens import GoogleLensError, search_google_lens_products
from .models import DetectedItem, DetectResponse, PinterestSearchResponse, Product, SearchResponse
from .pinterest import PinterestError, search_pinterest
from .zyte_client import ZyteClient

router = APIRouter(prefix="/api/trending", tags=["trending"])

# Process-local crop store: crop_id -> (jpeg bytes, stored_at). Single-user,
# stateless app — no DB — so a capped in-memory dict is enough; oldest
# entries are evicted once the cap is hit.
_CROP_STORE: dict[str, tuple[bytes, float]] = {}
_CROP_STORE_MAX = 200


def _store_crop(image_bytes: bytes) -> str:
    if len(_CROP_STORE) >= _CROP_STORE_MAX:
        oldest_id = min(_CROP_STORE, key=lambda k: _CROP_STORE[k][1])
        del _CROP_STORE[oldest_id]
    crop_id = uuid.uuid4().hex
    _CROP_STORE[crop_id] = (image_bytes, time.monotonic())
    return crop_id


class TrendingSearchRequest(BaseModel):
    idea: str
    n: int = 20


class DetectRequest(BaseModel):
    image_url: str


def _detect_and_store(image_bytes: bytes) -> DetectResponse:
    detections = detection.detect_items(image_bytes)

    items = []
    for d in detections:
        crop_bytes = crop_and_encode(image_bytes, d.box)
        crop_id = _store_crop(crop_bytes)
        items.append(DetectedItem(label=d.label, score=d.score, box=list(d.box), crop_id=crop_id))

    return DetectResponse(items=items)


@router.post("/search", response_model=PinterestSearchResponse)
async def trending_search(body: TrendingSearchRequest):
    if not body.idea.strip():
        raise HTTPException(400, "idea must not be empty")
    try:
        images = await search_pinterest(body.idea, n=body.n)
    except PinterestError as e:
        raise HTTPException(502, str(e))
    if not images:
        raise HTTPException(502, "No Pinterest results for that idea")
    return PinterestSearchResponse(images=images)


@router.post("/detect", response_model=DetectResponse)
async def trending_detect(body: DetectRequest):
    try:
        image_bytes = await detection.fetch_inspiration_image(body.image_url)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return _detect_and_store(image_bytes)


@router.post("/detect-upload", response_model=DetectResponse)
async def trending_detect_upload(file: UploadFile = File(...)):
    image_bytes = await file.read()
    return _detect_and_store(image_bytes)


@router.get("/fetch-image")
async def trending_fetch_image(url: str):
    # Proxied server-side so the browser can draw a Pinterest-hosted image onto
    # a <canvas> for manual cropping — a cross-origin <img> without this would
    # taint the canvas and block canvas.toBlob().
    try:
        image_bytes = await detection.fetch_inspiration_image(url)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return Response(content=image_bytes, media_type="image/jpeg")


@router.post("/search-lens", response_model=SearchResponse)
async def trending_search_lens(
    file: UploadFile = File(...),
    detected_item: str | None = Query(None, description="Trending provenance: detected item label"),
    inspiration_image_url: str | None = Query(None, description="Trending provenance: source Pinterest image"),
    enrich: bool = Query(
        True,
        description=(
            "Fetch each result's product page through Zyte for price, rating and "
            "review count. Adds ~20-60s; set false for the raw, faster image search."
        ),
    ),
):
    image_bytes = await file.read()
    # SerpApi when configured (seconds, and a real exact/visual split); the
    # Apify actor stays as the fallback so an unset key degrades rather than
    # breaks the feature.
    if serp_lens.is_configured():
        try:
            products, warnings = await serp_lens.search(image_bytes, file.content_type)
        except serp_lens.SerpLensError as e:
            return SearchResponse(results=[], warnings=[f"[Google Lens] {e}"])
    else:
        try:
            products, warnings = await search_google_lens_products(image_bytes, file.content_type)
        except GoogleLensError as e:
            return SearchResponse(results=[], warnings=[f"[Google Lens] {e}"])

    # Lens itself returns a title, a link and a picture — measured on a live
    # search, a price on 11 of 65 results and a rating on none. Everything the
    # workbench does (Opportunity Score, Market Snapshot, margin maths) needs
    # the numbers, so the product pages are read for them.
    if enrich and products:
        warnings += await product_page_enrich.enrich_from_product_pages(products)

    for p in products:
        p.detected_item = detected_item
        p.inspiration_image_url = inspiration_image_url
    return SearchResponse(results=products, warnings=[f"[Google Lens] {w}" for w in warnings])


class EnrichItem(BaseModel):
    title: str
    product_url: str
    site: str = "google_lens_extension"


class EnrichRequest(BaseModel):
    items: list[EnrichItem]


ENRICH_LIMIT = 24
_zyte_client = ZyteClient()


async def _enrich_one(item: EnrichItem) -> Product:
    product = Product(site=item.site, title=item.title, product_url=item.product_url)
    data = await _zyte_client.extract_product(item.product_url)
    if data:
        # Shared with the Lens path, so this endpoint gets rating and review
        # count too — it used to read only image and price — and the
        # low-confidence guard stays in one place.
        product_page_enrich.apply_zyte_product(product, data)
    return product


@router.post("/enrich", response_model=SearchResponse)
async def trending_enrich(body: EnrichRequest):
    """Fill in image/price for results that only have a title + link (e.g. the
    Lens browser-extension bridge) using Zyte API's AI-based automatic product
    extraction — no site-specific parser needed since the target can be any
    arbitrary e-commerce page."""
    items = body.items[:ENRICH_LIMIT]
    warnings = []
    if len(body.items) > ENRICH_LIMIT:
        warnings.append(f"Only enriching the first {ENRICH_LIMIT} of {len(body.items)} results")

    products = await asyncio.gather(*(_enrich_one(item) for item in items))
    # A card with no picture reads as broken in the UI, so results Zyte
    # couldn't find an image for are dropped rather than shown with a placeholder.
    results = [p for p in products if p.image_url]
    return SearchResponse(results=results, warnings=warnings)


@router.get("/crop/{crop_id}")
async def get_crop(crop_id: str):
    entry = _CROP_STORE.get(crop_id)
    if not entry:
        raise HTTPException(404, "Crop not found or expired")
    image_bytes, _ = entry
    return Response(content=image_bytes, media_type="image/jpeg")
