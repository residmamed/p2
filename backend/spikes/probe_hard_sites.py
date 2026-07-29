"""Temu, Costco and IKEA all failed the first sort probe. Find out why, and
whether a heavier Zyte mode fixes them, before designing a ranking around data
that may not exist.

    default productList  -> Temu 0 products, Costco 520 website-ban, IKEA only 3
    and on every site    -> productList returned price but NO rating/reviewCount

That last point matters most: the existing Popularity Score fallback is
rating x review_count, and if those fields never arrive the fallback has nothing
to compute from. So this probe also reports exactly which fields come back.

    python -m spikes.probe_hard_sites
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

ZYTE_URL = "https://api.zyte.com/v1/extract"
Q = "insulated+water+bottle"

VARIANTS = [
    ("auto", {}),
    ("browserHtml", {"productListOptions": {"extractFrom": "browserHtml"}}),
    ("browserHtml+geo US", {"productListOptions": {"extractFrom": "browserHtml"}, "geolocation": "US"}),
]

TARGETS = {
    "temu": f"https://www.temu.com/search_result.html?search_key={Q}",
    "costco": f"https://www.costco.com/CatalogSearch?keyword={Q}",
    "ikea": f"https://www.ikea.com/us/en/search/?q={Q}",
}

FIELD_KEYS = ["name", "price", "regularPrice", "aggregateRating", "mainImage", "url"]


async def probe(client: httpx.AsyncClient, site: str, url: str, label: str, extra: dict) -> str:
    payload = {"url": url, "productList": True, **extra}
    try:
        r = await client.post(ZYTE_URL, json=payload, auth=(settings.zyte_api_key, ""))
    except Exception as e:
        return f"  {label:<20} TRANSPORT {str(e)[:80]}"
    if r.status_code != 200:
        try:
            detail = r.json().get("title", "")
        except Exception:
            detail = r.text[:60]
        return f"  {label:<20} HTTP {r.status_code} {detail}"

    products = r.json().get("productList", {}).get("products", []) or []
    if not products:
        return f"  {label:<20} 0 products"

    present = {k for p in products[:10] for k in FIELD_KEYS if p.get(k) not in (None, "", {}, [])}
    sample = products[0]
    rating = sample.get("aggregateRating") or {}
    return (
        f"  {label:<20} {len(products)} products | fields: {', '.join(sorted(present))}\n"
        f"    first: {json.dumps({k: sample.get(k) for k in ('name', 'price')}, ensure_ascii=False)[:110]}\n"
        f"    rating obj: {json.dumps(rating, ensure_ascii=False)[:110] if rating else 'ABSENT'}"
    )


async def main() -> None:
    async with httpx.AsyncClient(timeout=180.0) as client:
        for site, url in TARGETS.items():
            print(f"\n{'='*70}\n{site}  {url}")
            lines = await asyncio.gather(
                *(probe(client, site, url, label, extra) for label, extra in VARIANTS)
            )
            for line in lines:
                print(line)


if __name__ == "__main__":
    asyncio.run(main())
