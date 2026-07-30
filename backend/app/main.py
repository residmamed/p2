import asyncio

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import bestsellers, claude_agent, lens_suppliers, sourcing
from .config import settings
from .google_lens import GoogleLensError, search_google_lens_products
from .image_match import match_and_group
from .models import (
    FindSuppliersResponse,
    Product,
    SearchResponse,
    Seller,
    SourcingResponse,
)
from .scrapers.alibaba import AlibabaScraper
from .scrapers.aliexpress import AliExpressScraper
from .scrapers.made_in_china import MadeInChinaScraper
from .trending import router as trending_router

app = FastAPI(title="Zyte Product Search")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(trending_router)

SCRAPERS = {
    "alibaba": AlibabaScraper(),
    "aliexpress": AliExpressScraper(),
    "made_in_china": MadeInChinaScraper(),
}
SITE_LABELS = {
    "alibaba": "Alibaba",
    "aliexpress": "AliExpress",
    "made_in_china": "Made-in-China",
}
ALL_SITES = list(SCRAPERS.keys())

MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp", "image/avif"}


def _normalize_title(title: str) -> str:
    return " ".join(title.strip().lower().split())


def _group_by_product(products: list[Product]) -> list[Product]:
    """Group listings that are the exact same product (same title) across sites,
    attaching every seller/provider found for it to the first listing's `sellers`."""
    groups: dict[str, list[Product]] = {}
    order: list[str] = []
    for p in products:
        key = _normalize_title(p.title)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(p)

    grouped: list[Product] = []
    for key in order:
        members = groups[key]
        primary = members[0]
        primary.sellers = [
            Seller(
                site=m.site,
                seller_name=m.seller_name,
                seller_url=m.seller_url,
                product_url=m.product_url,
                price_text=m.price_text,
                price_min=m.price_min,
                price_max=m.price_max,
                currency=m.currency,
                moq=m.moq,
                contact_type=m.contact_type,
                contact_value=m.contact_value,
            )
            for m in members
        ]
        grouped.append(primary)
    return grouped


def _parse_sites(sites: str | None) -> list[str]:
    if not sites:
        return ALL_SITES
    requested = [s.strip() for s in sites.split(",") if s.strip()]
    unknown = [s for s in requested if s not in SCRAPERS]
    if unknown:
        raise HTTPException(400, f"Unknown site(s): {', '.join(unknown)}. Valid sites: {', '.join(ALL_SITES)}")
    return requested or ALL_SITES


async def _run_site_text_search(site: str, query: str, page: int):
    label = SITE_LABELS[site]
    try:
        products, warnings = await SCRAPERS[site].search_by_text(query, page=page)
    except Exception as e:
        return [], [f"[{label}] Unexpected error: {e}"]
    for p in products:
        p.site = site
    return products, [f"[{label}] {w}" for w in warnings]


async def _run_site_image_search(site: str, image_bytes: bytes, content_type: str):
    label = SITE_LABELS[site]
    try:
        products, warnings = await SCRAPERS[site].search_by_image(image_bytes, content_type)
    except Exception as e:
        return [], [f"[{label}] Unexpected error: {e}"]
    for p in products:
        p.site = site
    return products, [f"[{label}] {w}" for w in warnings]


async def _run_google_lens_search(image_bytes: bytes, content_type: str):
    try:
        products, warnings = await search_google_lens_products(image_bytes, content_type)
    except GoogleLensError as e:
        return [], [f"[Google Lens] {e}"]
    return products, [f"[Google Lens] {w}" for w in warnings]


@app.get("/api/search/text", response_model=SearchResponse)
async def search_text(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    sites: str | None = Query(None, description="Comma-separated: alibaba,aliexpress,made_in_china"),
):
    site_list = _parse_sites(sites)
    results = await asyncio.gather(*(_run_site_text_search(s, q, page) for s in site_list))

    all_products = []
    all_warnings = []
    for products, warnings in results:
        all_products.extend(products)
        all_warnings.extend(warnings)

    # Same screening as /api/bestsellers: supplier-site keyword search answers
    # "tumbler" with lids and packaging as readily as with tumblers.
    if settings.claude_relevance_filter and all_products:
        screened = await claude_agent.filter_by_relevance(q, all_products)
        all_products = screened.kept
        all_warnings.extend(screened.warnings)

    return SearchResponse(results=_group_by_product(all_products), warnings=all_warnings)


@app.post("/api/search/image", response_model=SearchResponse)
async def search_image(
    file: UploadFile = File(...),
    sites: str | None = Query(None, description="Comma-separated: alibaba,aliexpress,made_in_china"),
    include_lens: bool = Query(
        False, description="Also cross-check with Google Lens reverse image search (slower, ~1-3 min)"
    ),
    detected_item: str | None = Query(None, description="Trending provenance: detected item label"),
    inspiration_image_url: str | None = Query(None, description="Trending provenance: source Pinterest image"),
):
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(400, f"Unsupported image type: {file.content_type}")

    image_bytes = await file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "Image too large (max 8MB)")

    site_list = _parse_sites(sites)
    tasks = [_run_site_image_search(s, image_bytes, file.content_type) for s in site_list]
    if include_lens:
        tasks.append(_run_google_lens_search(image_bytes, file.content_type))
    results = await asyncio.gather(*tasks)

    all_products = []
    all_warnings = []
    for products, warnings in results:
        for p in products:
            p.detected_item = detected_item
            p.inspiration_image_url = inspiration_image_url
        all_products.extend(products)
        all_warnings.extend(warnings)

    grouped, match_warnings = await match_and_group(all_products, image_bytes)
    return SearchResponse(results=grouped, warnings=all_warnings + match_warnings)


@app.post("/api/sourcing/image", response_model=SourcingResponse)
async def source_image(
    file: UploadFile = File(...),
    sites: str | None = Query(None, description="Comma-separated: alibaba,1688,made_in_china,aliexpress"),
    enrich: bool = Query(True, description="Also fetch each supplier's company page via Zyte"),
):
    """Photo -> Chinese supplier listings -> company profiles.

    Distinct from /api/search/image, which collapses everything into one card
    for the "who else sells this" view. This returns every candidate, tiered by
    visual confidence and carrying the supplier behind it — the sourcing view.
    See sourcing.py.
    """
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(400, f"Unsupported image type: {file.content_type}")

    image_bytes = await file.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "Image too large (max 8MB)")

    requested = [s.strip() for s in sites.split(",")] if sites else None
    if requested:
        unknown = [s for s in requested if s not in sourcing.SUPPLIER_SITES]
        if unknown:
            raise HTTPException(
                400,
                f"Unknown site(s): {', '.join(unknown)}. Valid: {', '.join(sourcing.SUPPLIER_SITES)}",
            )

    return await sourcing.source_by_image(
        image_bytes, file.content_type, sites=requested, enrich=enrich
    )


@app.post("/api/sourcing/by-url", response_model=SourcingResponse)
async def source_by_url(
    image_url: str = Query(..., description="Product photo URL, e.g. from a Best Seller Search result"),
    sites: str | None = Query(None, description="Comma-separated: alibaba,1688,made_in_china,aliexpress"),
    enrich: bool = Query(True),
):
    """The join between the two halves of the app: take a retail listing's photo
    and find who manufactures it. Same pipeline as /api/sourcing/image."""
    requested = [s.strip() for s in sites.split(",")] if sites else None
    if requested:
        unknown = [s for s in requested if s not in sourcing.SUPPLIER_SITES]
        if unknown:
            raise HTTPException(
                400,
                f"Unknown site(s): {', '.join(unknown)}. Valid: {', '.join(sourcing.SUPPLIER_SITES)}",
            )
    return await sourcing.source_by_image_url(image_url, sites=requested, enrich=enrich)


class FindSuppliersRequest(BaseModel):
    """Either field, never both. A URL is the fast path — nothing has to be
    published first — while base64 covers a photo that only exists on the
    caller's machine."""

    image_url: str | None = None
    image_base64: str | None = None
    # Read each supplier's own site — text and the pictures on it — for a
    # published email or phone. Off by default: several page fetches and a
    # vision call per supplier, so it turns a ~5s request into a ~30s one.
    include_contacts: bool = False


@app.post("/api/find-suppliers", response_model=FindSuppliersResponse)
async def find_suppliers(request: FindSuppliersRequest = Body(...)):
    """Photo -> Chinese supplier listings via Google Lens + Oxylabs, no browser.

    The fast counterpart to /api/sourcing/image: one SerpApi Lens call for the
    visual match, then concurrent Oxylabs fetches of each Alibaba/1688 product
    page for supplier, price and MOQ. Targets under 5s where the sourcing
    pipeline takes minutes; in exchange it only finds what Lens has indexed, and
    labels every row `lens_*_match` because nothing here compares the two
    products. See app/lens_suppliers.py.
    """
    try:
        return await lens_suppliers.find_suppliers(
            image_url=request.image_url,
            image_base64=request.image_base64,
            include_contacts=request.include_contacts,
        )
    except lens_suppliers.FindSuppliersError as e:
        # Bad input and a missing/unreachable Lens are different faults, and a
        # caller retrying a 400 forever because we returned 502 (or vice versa)
        # is a real cost. Configuration and upstream failures are ours (502).
        message = str(e)
        ours = any(
            hint in message
            for hint in ("not configured", "did not answer", "Image host", "Could not publish")
        )
        raise HTTPException(502 if ours else 400, message)


@app.get("/api/bestsellers", response_model=SearchResponse)
async def best_sellers(
    q: str = Query(..., min_length=1),
    sites: str | None = Query(
        None, description="Comma-separated, e.g. amazon,walmart,temu,target,wayfair"
    ),
):
    try:
        site_list = bestsellers.parse_sites(sites)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return await bestsellers.best_seller_search(q, site_list)


@app.get("/api/bestsellers/more", response_model=SearchResponse)
async def best_sellers_more(
    q: str = Query(..., min_length=1),
    site: str = Query(..., description="A single site id — this is per-store paging"),
    have: int = Query(0, ge=0, description="How many rows from this store are already shown"),
):
    """The next batch from one store, for its own "find more" button.

    An empty `results` list is the ordinary way of saying that store has nothing
    further — the caller shows "no more", not an error. `warnings` explains why
    when the reason is worth stating (paging depth reached, store returns
    everything at once).
    """
    if site not in bestsellers.SITES:
        raise HTTPException(
            400, f"Unknown site '{site}'. Valid: {', '.join(bestsellers.ALL_SITES)}"
        )
    return await bestsellers.more_from_site(q, site, have)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
