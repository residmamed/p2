"""One-off demo data builder. Reads a list of product URLs (tagged by site),
pulls real title/price/rating/image, downloads images locally, and emits
JS-ready MOCK_PRODUCTS entries.

Usage:
  python scripts/extract_products.py urls.txt
where urls.txt has one entry per line: "<site> <url>" or just "<url>"
(site is inferred from the domain when omitted). Costco lines may be given as
"costco <product_url> <image_url>" since Costco pages are not fetchable here.

Amazon/Walmart use Zyte's AI product extraction. Images are saved to
../frontend/public/product-images/<id>.jpg and referenced as /product-images/<id>.jpg.
"""

import asyncio
import base64
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.zyte_client import ZyteClient  # noqa: E402

IMG_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public" / "product-images"
IMG_DIR.mkdir(parents=True, exist_ok=True)

SITE_BY_HOST = {
    "amazon.com": "amazon", "walmart.com": "walmart", "temu.com": "temu",
    "costco.com": "costco", "ikea.com": "ikea", "pinterest.com": "pinterest",
}


def infer_site(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    for h, s in SITE_BY_HOST.items():
        if host.endswith(h):
            return s
    return "unknown"


async def save_image_via_zyte(z: ZyteClient, url: str, dest: Path) -> bool:
    try:
        r = await z.extract(url, browser_html=False, http_response_body=True)
        b64 = r.get("httpResponseBody", "")
        if not b64:
            return False
        dest.write_bytes(base64.b64decode(b64))
        return True
    except Exception:
        return False


async def save_image_direct(url: str, dest: Path) -> bool:
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.google.com/"}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
        if r.status_code == 200 and r.content:
            dest.write_bytes(r.content)
            return True
    except Exception:
        pass
    return False


async def extract_one(z: ZyteClient, idx: int, site: str, url: str, img_override: str | None):
    pid = f"{site}-{idx}"
    dest = IMG_DIR / f"{pid}.jpg"
    entry = {"id": pid, "site": site, "product_url": url}

    import re
    OG = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', re.I)
    OG2 = re.compile(r'<meta[^>]+content="([^"]+)"[^>]+property="og:image"', re.I)
    OGT = re.compile(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', re.I)

    if site in ("amazon", "walmart"):
        prod = await z.extract_product(url)
        if not prod:
            entry["_error"] = "no product from Zyte"
            return entry
        entry["title"] = (prod.get("name") or "").strip()
        price = prod.get("price")
        cur = prod.get("currencyRaw") or prod.get("currency") or "$"
        if price is not None:
            entry["price_text"] = f"{cur}{price}" if cur in ("$", "£", "€") else f"{cur} {price}"
        agg = prod.get("aggregateRating") or {}
        if agg.get("ratingValue") is not None:
            entry["rating"] = agg.get("ratingValue")
            entry["review_count"] = agg.get("reviewCount")
        img_override = img_override or (prod.get("mainImage") or {}).get("url")
    elif site == "pinterest":
        # Pins are inspiration images: grab og:image + og:title, no price.
        try:
            r = await z.extract(url, browser_html=True, max_retries=1)
            html = r.get("browserHtml", "")
        except Exception as e:
            entry["_error"] = f"pinterest fetch failed: {e}"
            return entry
        mt = OGT.search(html)
        entry["title"] = (mt.group(1).strip() if mt else "Pinterest inspiration")
        mi = OG.search(html) or OG2.search(html)
        img_override = img_override or (mi.group(1) if mi else None)

    # Download the image (direct first; Zyte fallback for blocked hosts).
    if img_override:
        ok = await save_image_direct(img_override, dest)
        if not ok:
            ok = await save_image_via_zyte(z, img_override, dest)
        entry["image_url"] = f"/product-images/{pid}.jpg" if ok else None
        if not ok:
            entry["_error"] = f"image download failed: {img_override[:60]}"
    else:
        entry["image_url"] = None
        entry.setdefault("_error", "no image url")
    return entry


async def main(path: str):
    z = ZyteClient()
    lines = [ln.strip() for ln in Path(path).read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    tasks = []
    counters: dict[str, int] = {}
    for ln in lines:
        parts = ln.split()
        # forms: "<url>" | "<site> <url>" | "<site> <url> <img>"
        if parts[0] in SITE_BY_HOST.values():
            site = parts[0]; url = parts[1]; img = parts[2] if len(parts) > 2 else None
        else:
            url = parts[0]; site = infer_site(url); img = parts[1] if len(parts) > 1 else None
        counters[site] = counters.get(site, 0) + 1
        tasks.append(extract_one(z, counters[site], site, url, img))

    results = await asyncio.gather(*tasks)
    ok = [r for r in results if not r.get("_error")]
    bad = [r for r in results if r.get("_error")]
    print(f"\n=== {len(ok)} ok, {len(bad)} failed ===")
    for r in bad:
        print("  FAIL", r["id"], r["_error"])
    Path("extracted_products.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print("wrote extracted_products.json")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "urls.txt"))
