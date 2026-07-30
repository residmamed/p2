# Zyte Product Search

Multi-site product search and sourcing tooling. Given a query (text or image), fan out to several e-commerce sites concurrently and return merged results.

## Language

**Product Search**:
The primary flow: a keyword returns a ranked grid of **live** retail products from Amazon, Walmart, Temu, Costco, IKEA, Target, Home Depot, eBay, Etsy, Best Buy and Wayfair, each ordered by the best demand signal that site actually offers (see [[rank-basis]]). Served by `/api/bestsellers`. There is **no mock data** in this app any more. [[google-shopping]] is selectable alongside the stores but is not one.
_Avoid_: Best seller search, demo mode

**Rank Basis**:
Which signal actually produced a product's position: `bestseller_sort` (the site's own best-selling order — Walmart, Target, Home Depot, and Amazon when it publishes no volume), `sold_count` (published "N sold" / "N bought in past month" — Amazon, Temu), `rating` (rating × review count), or `relevance` (page order only — Costco, IKEA, eBay, Etsy, Best Buy, Wayfair). Each carries a confidence weight applied before the cross-site merge, so a relevance-ordered row can't outrank a genuine best seller at equal Normalized Score. Always shown, never inferred — a site with no sort is labelled, not dressed up. A site that publishes ratings but offers no sort still stays on `relevance`: reordering by rating × reviews presents a ranking the store never made.
_Avoid_: Rank type, sort mode

**Google Shopping**:
A Product Search source that is not a store. The keyword goes to Pinterest, Pinterest's images go to Google Lens, and what comes back is whatever the web sells that looks like them — then narrowed to the listings whose titles actually describe the keyword (`app/google_shopping.py`). The narrowing is the source: without it, an image of a room returns the desk, the chair and the rug alongside the lamp. Always `relevance` under [[rank-basis]] — visual similarity says nothing about what sells.
_Avoid_: Google Lens tab (that's the photo search), Google Shopping API (SerpApi's `google_shopping` engine is not used)

**Manufacturer Search**:
The second step: from the selected products, reverse-image-search each product's photo on Alibaba, 1688 and Made-in-China to find who manufactures it. Triggered by the "Search for manufacturers" button; runs the live [[image-sourcing]] pipeline via `/api/sourcing/by-url`.
_Avoid_: Sourcing, supplier lookup

**Image Sourcing**:
The live photo → suppliers pipeline behind `POST /api/sourcing/image` and `/api/sourcing/by-url` (`app/sourcing.py`). Two transports, chosen per site. **Alibaba and 1688 go through Apify actors** (`app/apify_suppliers.py`) whenever the query is a URL — one call that both runs the site's reverse image search and names the seller. **Made-in-China and AliExpress, and any site searched from an uploaded file**, still take the browser route: Browserbase drives the site's own upload widget to get a results URL and Zyte extracts the listings. Zyte then fetches each seller's company page for a [[supplier-profile]]. Distinct from [[manufacturer-search]], which is the mock-data demo flow, and from `/api/search/image`, which collapses results into one card. Distinct again from [[lens-sourcing]], which answers the same question in seconds with no browser at all, and finds less.
_Avoid_: Reverse image search (that's the Google Lens path), Photo Search

**Lens Sourcing**:
The second, browserless photo → suppliers pipeline, behind `POST /api/find-suppliers` (`app/lens_suppliers.py`). Two REST calls and nothing else: SerpApi's Google Lens finds product pages hosting a matching image, then Oxylabs' Web Scraper API opens each Alibaba/1688 hit for supplier name, price and MOQ. Answers in seconds where [[image-sourcing]] takes minutes, because no browser drives an upload widget and no actor run is queued. In exchange it only finds what Lens has indexed — coverage of Chinese B2B listings is partial and varies by category, so an empty `results` here does not mean no supplier exists, and the response says so. Step 1 is cached 30 days by image hash; step 2 never is, because a month-old candidate URL is fine and a month-old price is not. Not a replacement for [[image-sourcing]] — a faster first look, which is why both endpoints exist.
_Avoid_: The Lens path (ambiguous — [[photo-search-google-lens]] is also Lens), Method 2 (the user's shorthand, not a term in the code)

**Lens Match Confidence**:
The only claim a [[lens-sourcing]] row makes about itself: `lens_exact_match` (Google Lens found the pixel-identical image file on that page) or `lens_visual_match` (it merely looks like it). Deliberately not a [[match-tier]] — nothing in that pipeline compares the two products, no hash distance is computed and no vision agent looks at the photographs, so a row there may not borrow the vocabulary of a stage that did. Provenance, never a score.
_Avoid_: Match score, confidence (both imply a number that was measured)

**Quantity Ladder**:
A B2B listing's tiered price: the same product costs less per unit the more you order (`$3.99` at 24–199 pieces, `$3.59` at 200–4,999, `$2.99` at 5,000+). The only honest source of price on an Alibaba product page, because the JSON-LD that page also publishes advertises a single `offers.price` which is the ladder's *bottom rung* — the rate at the largest quantity. Quoting that beside the MOQ states a price nobody can actually buy at, so [[lens-sourcing]] reports both ends of the ladder as a range and lets the JSON-LD figure win only when no ladder is published. Value and currency are always read from the same formatted string: the same URL returns dinar from one exit IP and dollars from another, so a number taken from the ladder and a currency taken from JSON-LD can report $452.71 for a $2.99 product. The localisation itself is pinned upstream — `oxylabs_client.GEO_FOR_SITE` fixes Alibaba's exit to the US, measured 6/6 USD against an unpinned mix of rand, lira and dollars — because a table quoting one supplier in rand and the next in dollars cannot be used to compare suppliers, which is its whole job. Pinned rather than converted: there is no exchange rate in this codebase and an invented one would put a wrong number on a supplier quote. 1688 and Taobao stay unpinned and quote CNY natively.
_Avoid_: Tiered pricing, bulk discount (neither says the price *is* the tier)

**Lens Redirect**:
What `type=exact_matches` actually returns: `lens.google.com/goto?url=<token>` rather than the destination. Measured 2026-07-29 — the token base64-decodes to protobuf-framed ciphertext with no plaintext URL in it, and fetching the wrapper server-side answers 404 with a browser user-agent. So the destination of a redirect-wrapped exact match is unknowable without opening it in a browser, which is the one thing [[lens-sourcing]] exists to avoid. Such a row is reported in `partial_matches` with a warning naming the marketplace and the count, never as a supplier result — a result nobody can open, whose supplier can never be read, is worse than an honest absence. Not every exact match is wrapped: direct links do arrive (a live run returned `arabic.alibaba.com/product-detail/...`) and those are enriched normally.

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
The stage that answers "who sells this?" by opening the listing's own product page (`app/supplier_resolve.py`). Now a fallback rather than the main event: the Apify actors behind [[image-sourcing]] name the seller and link its company page on every row they return (measured: 80 of 80 on one live search), and this stage skips any listing that already has one — so it runs only for the browser-route sites. A search-results card names the *product*, not the company — so before this existed, `seller_url` was `None` on every row of an image-sourced result and [[supplier-profile]] enrichment had nothing to enrich (measured: 49 Alibaba listings, 0 with any seller field). One Zyte call per listing returns `product.brand.name` (the manufacturer), the MOQ, and the rendered HTML carrying the seller's own company-page URL. Capped and run after [[match-basis]] vision matching, so page fetches are spent on listings already confirmed to be the buyer's product.
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
