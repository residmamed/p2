#!/usr/bin/env python3
"""Run the Lens Sourcing pipeline on one product image and print the JSON.

    cd backend
    python -m scripts.find_suppliers_demo https://example.com/product.jpg
    python -m scripts.find_suppliers_demo ./photo.jpg          # local file -> base64
    python -m scripts.find_suppliers_demo --no-cache <image>   # force a live Lens call

Calls app/lens_suppliers.py directly rather than going over HTTP, so it works
without a running uvicorn and the timings it prints are the pipeline's own
rather than the pipeline's plus a local round trip.

Needs SERPAPI_KEY in backend/.env. OXYLABS_USERNAME / OXYLABS_PASSWORD are
optional — without them step 2 is skipped and every row prints with
`enriched: false` and the reason, which is worth seeing at least once.
"""
import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import lens_suppliers  # noqa: E402


def _load(target: str) -> dict:
    if target.startswith(("http://", "https://")):
        return {"image_url": target}
    path = Path(target)
    if not path.is_file():
        raise SystemExit(f"Not a URL and not a file that exists: {target}")
    return {"image_base64": base64.b64encode(path.read_bytes()).decode("ascii")}


def _summarize(response) -> None:
    """A human-readable header before the JSON, because the numbers that matter
    for this pipeline are the timings and how many rows are actually enriched —
    both easy to lose in a hundred lines of output."""
    timings = response.step_timings
    upload = f"  upload {timings.upload_ms}ms" if timings.upload_ms is not None else ""
    contacts = f"  contacts {timings.contacts_ms}ms" if timings.contacts_ms is not None else ""
    cache = f"  (Lens from cache, {response.cache_age_days}d old)" if response.cached else ""
    enriched = sum(1 for r in response.results if r.enriched)

    print(f"\n{'=' * 72}", file=sys.stderr)
    print(
        f"total {response.latency_ms}ms{upload}  lens {timings.lens_ms}ms  "
        f"enrichment {timings.enrichment_ms}ms{contacts}{cache}",
        file=sys.stderr,
    )
    print(
        f"{len(response.results)} supplier listing(s), {enriched} enriched  |  "
        f"{len(response.partial_matches)} partial match(es)",
        file=sys.stderr,
    )
    for r in response.results:
        if r.supplier_name or r.supplier_url:
            print(f"  {r.supplier_name or '(unnamed)'} -> {r.supplier_url or '(no company page)'}",
                  file=sys.stderr)
        if r.contacts and not r.contacts.is_empty():
            c = r.contacts
            for kind in ("emails", "phones", "whatsapp", "wechat"):
                values = getattr(c, kind)
                if values:
                    print(f"      {kind}: {', '.join(values)}  [{c.found_in.get(kind)}]",
                          file=sys.stderr)
    for error in response.errors:
        print(f"  ERROR   {error}", file=sys.stderr)
    for warning in response.warnings:
        print(f"  warning {warning}", file=sys.stderr)
    print(f"{'=' * 72}\n", file=sys.stderr)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="product image URL, or a path to a local image file")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="skip the 30-day Lens cache and force a live SerpApi call",
    )
    parser.add_argument(
        "--contacts",
        action="store_true",
        help="also read each supplier's own site, pictures included, for an email or phone (slow)",
    )
    args = parser.parse_args()

    try:
        response = await lens_suppliers.find_suppliers(
            **_load(args.image),
            use_cache=not args.no_cache,
            include_contacts=args.contacts,
        )
    except lens_suppliers.FindSuppliersError as e:
        print(f"find-suppliers failed: {e}", file=sys.stderr)
        return 1

    _summarize(response)
    print(json.dumps(response.model_dump(), indent=2, ensure_ascii=False))
    # A run that found no supplier listings is not a crash, but it is not a
    # success either — exit non-zero so this is usable in a smoke check.
    return 0 if response.results else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
