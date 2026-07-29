# Zyte Product Search

Multi-site product search and sourcing tooling. Given a query (text or image), fan out to several e-commerce sites concurrently and return merged results.

## Language

**Product Search**:
The primary flow: a keyword returns a ranked grid of **live** retail products from Amazon, Walmart, Temu, Costco and IKEA, each ordered by the best demand signal that site actually offers (see [[rank-basis]]). Served by `/api/bestsellers`. There is **no mock data** in this app any more.
_Avoid_: Best seller search, demo mode

**Rank Basis**:
Which signal actually produced a product's position: `bestseller_sort` (the site's own best-selling order — Walmart, and Amazon when it publishes no volume), `sold_count` (published "N sold" / "N bought in past month" — Amazon, Temu), `rating` (rating × review count), or `relevance` (page order only — Costco, IKEA). Each carries a confidence weight applied before the cross-site merge, so a relevance-ordered row can't outrank a genuine best seller at equal Normalized Score. Always shown, never inferred — a site with no sort is labelled, not dressed up.
_Avoid_: Rank type, sort mode

**Manufacturer Search**:
The second step: from the selected products, reverse-image-search each product's photo on Alibaba, 1688 and Made-in-China to find who manufactures it. Triggered by the "Search for manufacturers" button; runs the live [[image-sourcing]] pipeline via `/api/sourcing/by-url`.
_Avoid_: Sourcing, supplier lookup

**Image Sourcing**:
The live photo → suppliers pipeline behind `POST /api/sourcing/image` (`app/sourcing.py`): Browserbase drives each supplier site's upload widget to get a results URL, Zyte extracts the listings from it, Zyte then fetches each seller's company page for a [[supplier-profile]]. Distinct from [[manufacturer-search]], which is the mock-data demo flow, and from `/api/search/image`, which collapses results into one card.
_Avoid_: Reverse image search (that's the Google Lens path), Photo Search

**Match Tier**:
The confidence label on an Image Sourcing result: `identical` (the same photo file), `exact` (same product, different shot), `similar` (same category), `unverified` (kept, but nothing could vouch for it). Which evidence produced it is the [[match-basis]], and the two are always shown together — a tier alone doesn't say whether anything actually looked at the pictures.
_Avoid_: Match score, confidence score

**Match Basis**:
How a [[match-tier]] was decided. `vision` — a Claude verdict on the query photo against the listing's photo, comparing the products rather than the pixels (`app/claude_agent.py`). `phash` — perceptual-hash distance alone: `identical` ≤6, `exact` ≤12, `similar` ≤20, beyond that `unverified`. Hashing recognises a *reused image file*, not a re-shot product, so on live runs every genuine match between a retail studio photo and a factory catalogue photo landed past distance 20 — which is why a `phash` tier is the weaker claim and is never presented as a confirmed match. A listing the vision agent calls a different product is dropped; one it never saw keeps its `phash` tier, because silence is "not looked at", never "rejected".
_Avoid_: Verified flag, match method

**Relevance Screening**:
The pass that removes what a site returned but the user didn't ask for, before anything is ranked (`app/claude_agent.py`). Every listing is judged `match`, `variant`, `accessory` or `unrelated` against the query; the last two are hidden and counted in a warning. A search for `tumbler` otherwise ships lids, straws, cleaning brushes and unrelated stock, and no scraped field distinguishes those from a tumbler — only reading the title does. Screening happens before the merge so a store's share of the [[combined-rank]] budget isn't spent on its own accessories, and the freed slots are never back-filled: the top-100 is a ceiling, not a quota.
_Avoid_: Filtering (too broad — the scrapers also "filter" sponsored rows), AI ranking (it removes rows, it doesn't order them)

**Supplier Resolution**:
The stage that answers "who sells this?" by opening the listing's own product page (`app/supplier_resolve.py`). A search-results card names the *product*, not the company — so before this existed, `seller_url` was `None` on every row of an image-sourced result and [[supplier-profile]] enrichment had nothing to enrich (measured: 49 Alibaba listings, 0 with any seller field). One Zyte call per listing returns `product.brand.name` (the manufacturer), the MOQ, and the rendered HTML carrying the seller's own company-page URL. Capped and run after [[match-basis]] vision matching, so page fetches are spent on listings already confirmed to be the buyer's product.
_Avoid_: Seller lookup, supplier scraping

**Supplier Profile**:
Company-level facts about a supplier — name, location, years active, business type, verified badge, and any publicly published email/phone/WhatsApp. Every field is absent rather than guessed when the page doesn't show it, and marketplace boilerplate addresses (`service@alibaba.com` and friends) are denylisted so they're never reported as the supplier's own.

Two sources, in preference order. The supplier's **own company page** gives the full profile — but on Alibaba it is bot-checked every time, through a plain fetch and a proxied cloud browser alike, so in practice it usually yields nothing. A challenged page is reported as challenged: parsing it produced "Captcha Interception" as the company name and a copyright range as the phone number, which is the confident-looking wrong answer this app exists to avoid. The **product page** is the fallback and is not challenged: it honestly supports company name and location, and nothing else — verification badges and years active are deliberately not taken from it, because those phrases appear throughout the marketplace's own page chrome and would be attributed to the wrong company.
_Avoid_: Seller info (that's the per-listing `Seller`, a different model)

**Best Seller Search**:
A query mode where the user submits a keyword and the app returns a single, merged ranking of the best-selling matches for that keyword across all configured retail sites (as opposed to browsing a site's site-wide bestseller chart).
_Avoid_: Trending, popular search

**Site Rank**:
A product's ordinal position within one site's own "best selling" (or equivalent) sort order for a given query. Used as the primary signal for Combined Rank when a site offers such a sort.

**Popularity Score**:
A fallback metric (derived from rating and review count) used to rank a product when its site has no "best selling" sort option to derive a Site Rank from.

**Normalized Score**:
A 0–1 value derived from a listing's Site Rank or Popularity Score, scaled relative to the other results returned by the *same site* for the *same query*. Makes ordinal (Site Rank) and numeric (Popularity Score) signals comparable across sites.

**Combined Rank**:
A product's final position in the merged, cross-site top-100 list for a query, determined by sorting all sites' listings together by Normalized Score.

**Shared Identifier**:
A UPC/EAN/GTIN/ASIN (or equivalent) confirmed present on two listings from different sites, used as the sole basis for treating them as the same product. Listings without a confirmed Shared Identifier are never merged, even if likely the same product.
_Avoid_: SKU match, fuzzy match

**Merged Listing**:
A single row in the Best Seller Search results representing one product, made up of one listing (the common case) or several listings from different sites that share a Shared Identifier.

**Result Cache**:
A short-TTL, query-keyed cache of Best Seller Search results. A query is served from cache when fresh, or triggers a live re-scrape on expiry/miss.

**Opportunity Score**:
A 0–100 workbench composite shown on retail product cards, answering "is this listing worth my attention within this result set?": 40% demand (log-scaled review count), 38% quality (rating), 22% value (price vs the set's median), each normalized against the same search's results. Products with no rating *and* no reviews (e.g. Pinterest pins) are unscoreable and show no chip — never a fake midpoint.
_Avoid_: Popularity Score (that's the Best Seller Search ranking fallback, a different formula)

**Pipeline**:
The persistent per-browser shortlist of saved products, organized as five stages: Researching → Contacted → Sampling → Approved / Dropped. Items carry notes, tags, a target unit cost and the query they came from. Lives in localStorage (`p2_shortlist`) — the app still has no database.
_Avoid_: Shortlist, watchlist, favorites

**Market Snapshot**:
The at-a-glance stats panel above search results: median price, price range and distribution, average rating, total reviews, median $/oz, site mix, and a Competition level (low/medium/high derived from median review depth — deep reviews = entrenched incumbents).

**Photo Search / Google Lens**:
Product Search's image entry point, launched from a camera icon *inside* the search bar (there is no separate Text/Photo mode toggle anymore). It runs the **real** Google Lens API — `searchBestSellersByImage` always hits the backend `/api/trending/search-lens` endpoint (the Apify `borderline~google-lens` actor), never the mock path, even though text search stays mocked. Each Lens hit's destination URL is mapped to a retail site (`siteForUrl`); the exact-match hits on the currently-selected sites are surfaced first (re-tagged to that site), and when those sites yield no exact match the 5 closest visual matches are shown instead — signalled to the UI via `lensMode` of `"exact"` or `"similar"`. Requires the backend running with `APIFY_TOKEN`.
_Avoid_: the old "AI Best Match" showcase — that feature has been removed.

**Margin Assumptions**:
The user's persisted per-unit cost model (freight $, duty %, channel fee %, fulfillment $) used by the Margin Calculator and the Est. margin column on supplier rows. Editable everywhere; "Save as my defaults" makes the edited values the new baseline.
