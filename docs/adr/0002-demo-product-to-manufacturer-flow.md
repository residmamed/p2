# Demo: mock-data Product Search → Manufacturer Search flow

## Context

We needed to show a demo to a user. The live scraping is unreliable in a demo
setting (Costco/Temu hard-ban via Zyte, ratings aren't extractable, prices need
per-site fixups), and the product framing changed: the app now leads with a
retail Product Search and treats supplier sourcing as a follow-up step.

## Decision

1. **The demo runs entirely on mock data** (`frontend/src/mockData.js`), gated by
   a `USE_MOCK` flag in `frontend/src/api.js`. No backend, no live scraping — the
   demo cannot fail in front of the user. Placeholder data ships now; real values
   come from the user's screenshots.
2. **Two-step flow in the Product Search tab** (the former Best Sellers tab):
   - Step 1 — Product Search across Amazon, Walmart, Temu, Pinterest, Costco,
     IKEA, shown as a plain grid badged by source (no combined ranking).
   - Step 2 — a "Search for manufacturers" button. With nothing selected it
     searches all products; with product checkboxes ticked it searches just
     those. Manufacturer results are supplier listings from Alibaba, 1688 and
     Made-in-China, grouped per product.
3. **Alibaba / AliExpress / Made-in-China are removed as primary search sources.**
   The old sourcing Search tab is hidden from the nav (files kept for revert).
   Alibaba/1688/Made-in-China now appear only as *manufacturer* results.

## Consequences

- The live Best Seller ranking backend (`backend/app/bestsellers.py`, ADR-0001)
  and its terms (Site Rank, Popularity Score, Combined Rank) still exist but are
  **dormant** — `USE_MOCK` bypasses them. A future reader seeing ranking code but
  a non-ranked demo UI should look here. Flip `USE_MOCK` to false to restore the
  live path.
- Product and manufacturer cards share one `ProductCard`; retail vs. manufacturer
  rendering is chosen by `isRetailSite(site)` (rating + no contact vs. seller name
  + MOQ + contact link).
