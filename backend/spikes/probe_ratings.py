"""Walmart and IKEA come back with no rating/review data.

Zyte's productList exposes an `aggregateRating` field but leaves it empty for
both sites (measured in probe_hard_sites.py), so the question is whether the
data is absent from the page or merely absent from Zyte's extraction. Modern
retail SPAs embed their full product model as JSON in the HTML — Walmart in
__NEXT_DATA__, IKEA in its own bundle — so this looks directly.

Prints, per site: whether productList returned ratings, and whether the raw HTML
contains rating-shaped keys with plausible values.

    python -m spikes.probe_ratings
"""
import asyncio
import base64
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.zyte_client import ZyteClient  # noqa: E402

Q = "insulated+water+bottle"
TARGETS = {
    "walmart": f"https://www.walmart.com/search?q={Q}&sort=best_seller",
    "ikea": f"https://www.ikea.com/us/en/search/?q={Q}",
}

# Key names these sites plausibly use for the two fields we want.
RATING_KEYS = ["averageRating", "average_rating", "aggregateRating", "ratingValue",
               "rating", "avgRating", "starRating", "reviewRating"]
COUNT_KEYS = ["numberOfReviews", "numReviews", "reviewCount", "ratingCount",
              "totalReviewCount", "reviewsCount", "ratings_total"]

OUT = Path(__file__).parent / "out" / "ratings"


def find_keys(text: str, keys: list[str]) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for k in keys:
        # "key":4.6  /  "key":"4.6"  /  "key": 1234
        found = re.findall(rf'"{k}"\s*:\s*"?([\d.]+)"?', text)
        if found:
            hits[k] = found[:5]
    return hits


async def probe(zyte: ZyteClient, site: str, url: str) -> None:
    print(f"\n{'='*70}\n{site}")
    OUT.mkdir(parents=True, exist_ok=True)

    items = await zyte.extract_product_list(url)
    rated = sum(1 for i in items if i.get("aggregateRating"))
    print(f"  productList: {len(items)} items, {rated} with aggregateRating")

    for mode in ("httpResponseBody", "browserHtml"):
        try:
            result = await zyte.extract(
                url,
                browser_html=(mode == "browserHtml"),
                http_response_body=(mode == "httpResponseBody"),
            )
        except Exception as e:
            print(f"  {mode:<18} FAILED {str(e)[:70]}")
            continue

        if mode == "browserHtml":
            html = result.get("browserHtml", "")
        else:
            body = result.get("httpResponseBody", "")
            html = base64.b64decode(body).decode("utf-8", "replace") if body else ""

        if not html:
            print(f"  {mode:<18} empty")
            continue

        (OUT / f"{site}-{mode}.html").write_text(html)
        r_hits = find_keys(html, RATING_KEYS)
        c_hits = find_keys(html, COUNT_KEYS)
        print(f"  {mode:<18} {len(html):>8} bytes")
        print(f"    rating keys: {r_hits or 'NONE'}")
        print(f"    count keys:  {c_hits or 'NONE'}")

        # Is there an embedded JSON blob we could parse properly?
        for marker in ("__NEXT_DATA__", "__PRELOADED_STATE__", "window.__APP",
                       'type="application/ld+json"', "application/json"):
            if marker in html:
                print(f"    embedded blob: {marker}")


async def main() -> None:
    zyte = ZyteClient()
    for site, url in TARGETS.items():
        try:
            await probe(zyte, site, url)
        except Exception as e:
            print(f"  {site} FAILED: {e}")
    print(f"\nartifacts in {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
