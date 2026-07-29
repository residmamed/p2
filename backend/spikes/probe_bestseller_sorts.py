"""Find out which retail sites actually honour a best-seller sort in a plain URL.

Guessing sort params produces a ranking that looks authoritative and isn't, so
this asks the sites directly: fetch the default search URL and each candidate
sorted URL through Zyte's productList extraction, then compare the top titles.

Different top-5 => the param changed the ordering => that site is Tier 1 (list
position is a real Site Rank). Same top-5 => the param was ignored, and the site
must fall back to a demand signal instead of pretending to be sorted.

    python -m spikes.probe_bestseller_sorts
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.zyte_client import ZyteClient  # noqa: E402

QUERY = "insulated water bottle"
Q = QUERY.replace(" ", "+")

CANDIDATES: dict[str, list[tuple[str, str]]] = {
    "walmart": [
        ("default", f"https://www.walmart.com/search?q={Q}"),
        ("sort=best_seller", f"https://www.walmart.com/search?q={Q}&sort=best_seller"),
    ],
    "amazon": [
        ("default", f"https://www.amazon.com/s?k={Q}"),
        ("s=exact-aware-popularity-rank", f"https://www.amazon.com/s?k={Q}&s=exact-aware-popularity-rank"),
        ("s=review-rank", f"https://www.amazon.com/s?k={Q}&s=review-rank"),
    ],
    "temu": [
        ("default", f"https://www.temu.com/search_result.html?search_key={Q}"),
        ("opt_sort=sales", f"https://www.temu.com/search_result.html?search_key={Q}&opt_sort=sales"),
        ("search_sort=6", f"https://www.temu.com/search_result.html?search_key={Q}&search_sort=6"),
    ],
    "costco": [
        ("default", f"https://www.costco.com/CatalogSearch?keyword={Q}"),
        ("sortBy=item_popularity", f"https://www.costco.com/CatalogSearch?keyword={Q}&sortBy=item_popularity"),
        ("sortBy=featured", f"https://www.costco.com/CatalogSearch?keyword={Q}&sortBy=featured"),
    ],
    # Six values probed on 2026-07-29, none usable. BEST_SELLER, POPULARITY and
    # TOP_SELLER return the default order unchanged (the param is ignored);
    # MOST_POPULAR, RATING and BESTSELLER return a different set that no longer
    # matches the query at all — "mirror" came back as dressers, a PAX frame and
    # a remote control, i.e. the sort discarded the search rather than reordering
    # it. So IKEA stays relevance-ranked; there is no popularity signal to take.
    "ikea": [
        ("default", f"https://www.ikea.com/us/en/search/?q={Q}"),
        ("sort=BEST_SELLER", f"https://www.ikea.com/us/en/search/?q={Q}&sort=BEST_SELLER"),
        ("sort=POPULARITY", f"https://www.ikea.com/us/en/search/?q={Q}&sort=POPULARITY"),
        ("sort=TOP_SELLER", f"https://www.ikea.com/us/en/search/?q={Q}&sort=TOP_SELLER"),
        ("sort=MOST_POPULAR", f"https://www.ikea.com/us/en/search/?q={Q}&sort=MOST_POPULAR"),
        ("sort=RATING", f"https://www.ikea.com/us/en/search/?q={Q}&sort=RATING"),
    ],
}


async def fetch(zyte: ZyteClient, label: str, url: str) -> tuple[str, list[str], dict]:
    try:
        items = await zyte.extract_product_list(url)
    except Exception as e:
        return label, [], {"error": str(e)[:120]}
    titles = [(i.get("name") or "")[:60] for i in items[:5]]
    # Which demand-ish fields does productList actually return for this site?
    present = set()
    for i in items[:10]:
        for k in ("aggregateRating", "rating", "reviewCount", "price"):
            if i.get(k) not in (None, "", {}):
                present.add(k)
    return label, titles, {"count": len(items), "fields": sorted(present)}


async def main() -> None:
    zyte = ZyteClient()
    for site, variants in CANDIDATES.items():
        print(f"\n{'='*70}\n{site}")
        results = await asyncio.gather(*(fetch(zyte, l, u) for l, u in variants))
        baseline: list[str] | None = None
        for label, titles, meta in results:
            if meta.get("error"):
                print(f"  {label:<32} ERROR {meta['error']}")
                continue
            if baseline is None:
                baseline = titles
                verdict = "(baseline)"
            elif not titles:
                verdict = "NO PRODUCTS"
            elif titles == baseline:
                verdict = "IGNORED — same order as default"
            else:
                verdict = "*** SORT APPLIED ***"
            print(f"  {label:<32} n={meta.get('count', 0):<3} {verdict}")
            print(f"    fields: {', '.join(meta.get('fields', [])) or 'none'}")
            for t in titles[:3]:
                print(f"      - {t}")


if __name__ == "__main__":
    asyncio.run(main())
