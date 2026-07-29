# Zyte Product Search — Alibaba, AliExpress & Made-in-China

Search Alibaba, AliExpress, and Made-in-China at once by keyword or by uploading a product photo. Results show image, price, MOQ (where applicable), seller name, and a way to contact the seller — a raw phone/email/WhatsApp when a site publicly shows one, otherwise a link to that site's own contact/inquiry form (which is the common case on all three sites today).

## Setup

**Backend**
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env   # ZYTE_API_KEY (required); ANTHROPIC_API_KEY (relevance
                       # screening + visual supplier matching); APIFY_TOKEN
                       # (optional, only for Trending)
uvicorn app.main:app --port 8000
```

**Frontend**
```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

## How it works

Each site has a `Scraper` implementation (`app/scrapers/{alibaba,aliexpress,made_in_china}.py`) behind a shared interface (`app/scrapers/base.py`). `/api/search/text` and `/api/search/image` fan out to all selected sites concurrently (`sites=alibaba,aliexpress,made_in_china` query param, defaults to all three) and merge the results, tagging each `Product` with its `site`.

**Text search** — Alibaba and AliExpress both embed a full product-data JSON blob directly in the search-results page (`window.__page__data_sse10._offer_list` and `window._dida_config_._init_data_` respectively) fetched via Zyte's managed browser rendering (`browserHtml`) with retry-on-captcha. Made-in-China is a plain server-rendered HTML site with no anti-bot captcha (just request throttling), so it uses Zyte's cheaper `httpResponseBody` mode and is parsed with CSS selectors (`parsel`).

**Image search** — none of the three sites' upload widgets can be driven through Zyte's own managed browser `actions` API: it has no file-upload action, and JS-dispatched synthetic file-input events get rejected by these sites' upload handlers (real browsers mark such events `isTrusted: false`). Instead, a real Playwright/Chromium browser (routed through Zyte's raw HTTPS proxy at `api.zyte.com:8014` for IP reputation) drives the actual upload UI and calls Playwright's `set_input_files`, which produces trusted native browser events. For Alibaba this browser session also scrapes the rendered results directly. For AliExpress and Made-in-China, the upload instead redirects the browser to a plain results URL (`.../w/wholesale-....html?isNewImageSearch=y&imageId=...` / `.../img-search/<id>.html`) — once that URL is captured, the actual results are fetched via the same cheap Zyte pipeline used for text search, reusing the same parser.

## Image sourcing: photo → Chinese suppliers → company profiles

`POST /api/sourcing/image` (`app/sourcing.py`) answers "who manufactures this?" from a photo, across Alibaba, 1688, Made-in-China and AliExpress. Five stages, with a deliberate tool boundary — a real browser only where nothing else can get in, page fetches only on listings already worth the money:

1. **Discover — Browserbase** (`app/scrapers/image_discovery.py`). A cloud browser drives each site's photo-upload widget and stops the moment the site hands back a results URL. A real browser is needed only because these upload handlers reject synthetic file events (`isTrusted: false`) and Zyte's `actions` API has no file-upload action — so the browser does that one thing and nothing else. It never parses products.
2. **Extract — Zyte.** The captured results URL is fetched through the same cheap pipeline text search already uses, and run through the existing per-site parsers. When a parser finds nothing, it falls back to Zyte's `productList` AI extraction — which is also the only path for **1688**, where a hand-written parser against an unverified DOM would be more fragile than letting Zyte adapt.
3. **Verify — Claude vision** (`app/claude_agent.py`). Hashing recognises a *reused image file*, not a re-shot product: on a live run, every genuine match between an Amazon studio photo and Made-in-China catalogue photos landed past distance 20, so the grid could only ever say "unverified" for the normal case. So the top listings (dealt round-robin across sites, `VISION_TOP_N`) go to a vision model with the query photo, which judges the *objects* — `same_product`, `same_model_variant`, `same_category`, `different` — and sets the tier from its verdict. Measured on an Owala tumbler photo: 0 confident phash matches → **16 Alibaba suppliers confirmed as the same product**, each with the feature that decided it ("Identical white body, tan lid, brown seal ring"). A listing it calls `different` is dropped; one it never saw keeps its phash tier. Every row carries `match_basis` (`vision` / `phash`) so the UI shows a tick only where the photos were actually compared — a hash coincidence and a judgement must not look alike.
4. **Resolve the supplier — Zyte** (`app/supplier_resolve.py`). **A search-results card names the product, not the company.** Without this stage `seller_url` is `None` on every row and stage 5 has nothing to enrich — measured on a live Alibaba run: *49 listings, 0 with any seller field*. The listings were fine; nobody had opened them. One Zyte call per listing asks for `product` and `browserHtml` together (one page fetch, not two) and yields `brand.name` — the manufacturer — plus the MOQ from `additionalProperties`, plus the seller's own company-page URL from the HTML. Capped at `RESOLVE_TOP_N` and run *after* visual matching, so page fetches are spent on listings already confirmed to be the buyer's product. Result on the same run: **0 → 12 suppliers named, 12 linked to a company page.**
5. **Scan the company — Zyte** (`app/supplier_profile.py`). Not one page: the scan walks the supplier's own site (contact page, company profile, about, index — `MAX_PAGES_PER_COMPANY`, fetched concurrently) and merges the findings, because different pages publish different channels. Emails come from `mailto:` links and body text; WhatsApp from `wa.me` links; phones from `tel:` links, falling back to text **only immediately after an explicit phone label**.

   **What Alibaba actually publishes — measured, not assumed.** Scanning four pages of each supplier's site returned *no* email, phone or WhatsApp for any of them. That is not a scraping failure: the product page's own JSON serves `contactEncryptId` (the contact identity, **encrypted**) and `supplierOperationalAddress` as the literal placeholder `INTL_ONSITE`. The details aren't on a page we failed to find — they're withheld from anonymous visitors by design, and contact runs through the marketplace enquiry form. The scan says exactly that, and distinguishes it from "we couldn't look":

   > scanned 4 page(s) of this supplier's site; no email, phone or WhatsApp is published publicly — contact goes through the marketplace enquiry form

   Three wrong answers were removed along the way, each of which looked like data: a bot-check page reported as the company `Captcha Interception`; a screen resolution (`1920-1200`) and an internal id (`429-256778`) reported as phone numbers; and the minisite's generic `<title>` (`Alibaba.com`) overwriting every real company name. Contacts still surface normally on sites that do publish them — that path was also fixed end-to-end, since the UI read `s.email`/`s.phone` which the API layer never populated.

**Results are tiered, and the tier says what decided it.** Each listing's thumbnail is phash-compared against the query photo: `identical` (distance ≤ 6 — the same photo file, which happens often because factory photos get reused up the supply chain), `exact` (≤ 12), `similar` (≤ 20), `unverified` beyond that. Ranking puts tier before price — the cheapest listing is worthless if it isn't the same product. Listings whose thumbnail failed to load are kept but left untiered, never scored as if verified.


This replaces the local-Playwright image path for these sites: no Chromium in the backend, and concurrent searches no longer contend over one local browser. Set `BROWSERBASE_API_KEY` / `BROWSERBASE_PROJECT_ID`; unset, the endpoint returns a warning saying so rather than failing opaquely.

- **Recipe provenance is tracked** in `image_discovery.VERIFIED`. `made_in_china` and `alibaba` are ported from the working local scrapers; `aliexpress` uses the results-URL shape below; **`1688` is unverified** — run `python -m spikes.tune_image_recipe 1688 photo.jpg` first, which dumps per-stage screenshots, the final URL and page HTML so its selectors can be corrected.
- **Concurrency is capped at 3** cloud browsers (`DISCOVERY_CONCURRENCY`) — Browserbase plans cap concurrent sessions and exceeding the cap fails session creation outright rather than queueing.
- **Supplier resolution and enrichment are both limited to the top 12** ranked listings; enriching all of them would multiply Zyte calls by ~40 per search for rows nobody scrolls to.

## Relevance screening: only the product you searched for

A keyword search returns whatever the site's matcher liked. Searching `tumbler` on Temu, Costco and IKEA came back with **103 listings, of which 57 were not tumblers** — lids, straws, cleaning brushes, cup-holder expanders and outright unrelated stock. Nothing scraped tells those apart from a tumbler: a lid has a rating, a price, a sold count and a page position just like the product does. Only reading the title does.

So before anything is ranked, every listing is judged against the query (`app/claude_agent.py`) as `match`, `variant`, `accessory` or `unrelated`; the last two are hidden and the count reported:

> Hid 57 listing(s) the sites returned but that aren't what you searched for (52 unrelated to 'tumbler', 5 accessories/parts rather than the product). The remaining 46 are the real matches — the result count is not padded back up.

Three properties this is built around:

- **The freed slots are never back-filled.** The top-100 is a ceiling, not a quota. A query with 12 real matches returns 12 rows.
- **Screening runs before the merge**, so dedupe, the rank-basis weighting and the round-robin TOP_N deal all operate on real matches — a store's share of the budget isn't spent on its own accessories.
- **Every uncertainty resolves towards showing the row.** No key, a failed batch, a skipped index, an ambiguous title: all keep the listing. The failure mode of a filter is silent deletion, which is worse than the unfiltered list it replaces. Applies to `/api/bestsellers` and `/api/search/text`; set `CLAUDE_RELEVANCE_FILTER=false` to switch it off without a deploy.

## Picture search: Google Lens via SerpApi

The camera icon in the search bar runs a real reverse-image search (`app/serp_lens.py`, `POST /api/trending/search-lens`). SerpApi replaced the Apify `borderline~google-lens` actor: **~6s per search instead of 2-3 minutes**, a plain GET instead of start-then-poll, and the exact/visual split as a first-class `type` parameter that maps straight onto the app's existing `google_lens_exact` / `google_lens` tags.

    type=exact_matches    pages hosting the pixel-identical image   -> ~400 hits
    type=visual_matches   things that merely look like it           ->  ~59 hits

Both run concurrently; 25 exact + 40 visual are kept. Matches with no picture are dropped rather than shown with a placeholder.

**The upload constraint.** SerpApi's Lens endpoint accepts only a publicly reachable image URL — no upload, no base64, no multipart. An uploaded photo therefore has to be published somewhere Google can fetch it. The current host is **uguu.se** (anonymous, no credentials, files expire in hours), which is a pragmatic default and not the right long-term answer: every uploaded photo transits a third party we don't control. `_publish()` is the single seam — point it at an S3/R2 bucket to remove that dependency. Apify key-value stores were tried first and rejected: their records return 403 without a token, and putting the token in a URL handed to SerpApi would leak it.

The Apify actor remains as the fallback when `SERPAPI_KEY` is unset, so the feature degrades rather than breaks.

**Lens results are enriched from their product pages.** Lens itself returns a title, a link and a picture and little else — measured on a live search, **a price on 11 of 65 results and a rating on none**, which left the whole workbench (Opportunity Score, Market Snapshot, margin maths) with nothing to compute from. So each result's page is fetched through Zyte's AI `product` extraction, which needs no site-specific parser — necessary here, since Lens lands on whatever host Google found (Target, eBay, seven different Kroger-family grocery chains). Measured on the same search:

| | Before | After |
|---|---|---|
| Numeric price, across all 65 | 0 | 22 |
| Numeric price, of the 24 fetched | 0 | 18 |
| Rating or review count | 0 | 8 |

- **Social and video hosts are skipped** — TikTok, Instagram, Facebook, YouTube and friends were 18 of those 65 results, and have no price or rating to find. Fetching them would spend a Zyte request each to learn nothing.
- **Exact matches get the budget first**, since that's what the UI surfaces before the merely-similar ones.
- **The ceiling is 24 pages, all fetched at once.** These pages take 13–88s each, so wall time is the slowest single one rather than a multiple of it: 24 concurrent under a 60s cap beat twelve-at-a-time under a 40s cap on *both* speed and completeness. Total search goes from ~4s to **~80s** — pass `enrich=false` for the raw, fast image search.
- **Ratings stay sparse and that's the sites, not the fetch.** 16 of 24 pages publish no rating at all; the warning says so, so a half-empty column isn't read as a bug.
- **A page price beats the image search's price.** Lens's comes from Google's cached snippet, Zyte's off the live page. Ratings are only ever filled in, never overwritten.

## Trending: Idea → Pinterest → item detection → supplier search

A second mode (tab toggle in the UI) for discovery rather than direct lookup: type an idea (e.g. "mid century modern bedroom"), get real Pinterest inspiration images, click one, and a local **YOLO-World** object-detection model (`ultralytics`, `app/detection.py`) finds individual items in the scene (bed, lamp, nightstand, pillow, ...) and crops each one out (`app/crop.py`, Pillow). Pick item(s) + sites, and each crop is searched exactly like a manually uploaded photo — this mode adds **no new search infrastructure**, it just feeds crops into the existing `/api/search/image` endpoint (with two optional provenance query params so results carry a "via `<item>` · Pinterest" badge).

- **Pinterest** (`app/pinterest.py`) goes through Apify's `fetch_cat~pinterest-search-scraper` actor, not direct scraping — confirmed live during development that Pinterest's search page is genuinely paywalled for logged-out sessions (empty result data + a Log in/Sign up wall), unlike the three supplier sites which just have anti-bot friction. Needs `APIFY_TOKEN` in `.env`; the Trending tab is otherwise unused/degrades to an error if unset.
- **Detection** runs in-process (not a separate sidecar — this backend is already Python/FastAPI) using `yolov8s-worldv2.pt` + its CLIP text-encoder weights, both expected at `backend/yolov8s-worldv2.pt` / `backend/weights/clip/ViT-B-32.pt` (gitignored — `ultralytics` auto-downloads them on first use if missing, ~360MB combined, one-time).
- No persistence: this app has no database, so a picked image's detections aren't saved — refreshing/reselecting re-runs detection. Cropped images live in a small in-memory, capped LRU-ish dict (`_CROP_STORE` in `app/trending.py`) keyed by an opaque `crop_id`, not written to disk.

## Product Search: one merged top-100 across five retail sites

*What's already selling at retail for this keyword?* — across **Amazon, Walmart, Temu, Costco and IKEA**. `/api/bestsellers?q=...&sites=...` fans out concurrently and returns one merged, ranked top-100 (`app/bestsellers.py`). **No mock data anywhere** — the frontend calls this endpoint directly.

Every site's capability below was **probed live**, not assumed (`spikes/probe_bestseller_sorts.py`, `spikes/probe_hard_sites.py`):

| Site | Transport | Rank basis | Evidence |
|---|---|---|---|
| Amazon | **Rainforest** + `sort_by=bestseller_rankings` | `sold_count` | The sort **reorders** (Owala and Stanley lead, which are in fact the best sellers). Returns `recent_sales` ("20K+ bought in past month"), **exact** review counts, rating and ASIN. Falls back to **SerpApi** `engine=amazon`, then to Zyte |
| Walmart | **SerpApi** `engine=walmart` + `sort=best_seller` | `bestseller_sort` | The sort **reorders**. Returns a typed `offer_price` (separate from `was_price`), rating, review count and `us_item_id` in **one** request. Falls back to Zyte `productList` + `__NEXT_DATA__` |
| IKEA | Zyte `productList` + product-page JSON-LD | `relevance` | `sort=BEST_SELLER` is **ignored**. Search results carry no ratings, but each product page ships schema.org `aggregateRating` — fetched per product, affordable because IKEA returns only ~4 results |
| Temu | **Apify** `amit123/temu-products-scraper` | `sold_count` | Zyte returns **0 products** in every mode and Browserbase hits a "Security verification" wall. The actor returns `sales_num` ("46K+") — real units sold — plus rating/review count nested inside `comment` (`goods_score`, `comment_num_tips`) |
| Costco | **Apify** `e-commerce/costco-fast-product-scraper` | `rating` | Zyte returns **HTTP 520 Website Ban**. The actor returns `rating` + `reviewsCount` but no sales figures |

- **Default ordering is grouped by store**, best-selling first within each. A single interleaved list answers "what sells best overall", but the workflow is per-store — you compare Amazon's top sellers against each other, then Walmart's. Grouping also keeps each store's ordering internally honest: within one store every row shares a Rank Basis, so the comparison is like-for-like. `site_rank` is the position within a store, `combined_rank` the position overall.
- **Walmart moved to SerpApi because Zyte's prices were wrong 13–26% of the time.** Head-to-head on two live queries, verified against the product pages themselves:

  | | Zyte `productList` | SerpApi |
  |---|---|---|
  | Requests per search | 2 | 1 |
  | Ratings in the results | **0 of 41** | 40 of 40 |
  | Prices disagreeing with the page | 13% and **26%** | 0 |

  Two distinct failure modes, both confirmed on the product pages. Zyte reported the **struck-through pre-Rollback price as the current one** — VEAT00L earbuds at `$159.98` against an actual `$20.49`; in 7 of 9 misses its value equalled SerpApi's `was_price` exactly. And it **truncated the cents off three-digit prices** (`249.99` arriving as `249.0`), which the cents heuristic below then read as a cents figure and divided, listing a **$249.99 item at $2.49** and a $168.00 one at $1.68. A typed `offer_price` removes the whole class of error rather than retuning a threshold.
- **The Walmart fallback reads products from `__NEXT_DATA__`, and gets prices and ratings from two places each.** The blob carries stars, review counts, `usItemId` and clean titles but ships every price as `0` (Walmart fetches them client-side). Two defects here surfaced as *"Walmart shows nothing for reviews and 0 for prices"*, and both were reading data that was already on the page:

  - **Ratings arrive in two shapes.** The blob has a flat `averageRating`/`numberOfReviews` pair *and* a nested `rating: {averageRating, numberOfReviews}` object, and items don't reliably carry both — measured on one live search, the flat pair was present on 32 of 42 items and the nested object on 40. The parser read only the flat pair, silently dropping the stars off a fifth of the rows. It now falls back to the nested object, and treats a `0` rating as "not rated yet" rather than one star.
  - **Prices need both sources, because neither covers the page alone.** The obvious fix — join `productList` prices onto the blob's products by item id — tops out around half the rows, and *not* because the ids fail to match (they overlap 36 of 40 on a good run): the two requests genuinely return different result sets, and the overlap swings run to run. The rendered search page is the missing half: it is the same page the blob came from, so its painted prices belong to exactly those products. Indexing both and preferring the rendered one (it needs no cents heuristic to interpret) took coverage from ~42% to ~67% on live searches. The remainder are mostly grocery items Walmart won't price until you pick a store, and those stay honestly blank — `$0.00` is rejected outright, since a zero price passes every has-a-price check and then drags the Market Snapshot median and the margin calculator to nothing.

  This is the fallback path only; Walmart normally comes from SerpApi in one request, which returns prices and ratings complete (40/40 and 38/40 on a live check).
- **An empty `productList` is retried once.** Zyte returns HTTP 200 whether a search genuinely has no matches or the page hadn't finished rendering, so the two are indistinguishable. IKEA is the case that forced it: a client-rendered search page that took **7–52s across otherwise identical runs**, occasionally reporting nothing where four consecutive probes of the same query each returned 9 products. One extra request, only on an empty result, and the warning then names both possibilities instead of asserting the site has nothing.
- **A structured API returning zero results says so.** It used to fall through to Zyte silently, so the user saw the fallback's complaints with nothing explaining why the better source went unused.
- **Amazon uses Rainforest because SerpApi rounds review counts.** `131,434` arrives from SerpApi as `131,400`, `53,997` as `53,900` — three significant figures, with no option to turn it off. Both APIs return the same best-seller ordering and the same purchase-volume figures, so precision is the whole difference; Rainforest is also the cheaper of the two per request. SerpApi stays as Amazon's fallback for a spent Rainforest quota.
- **One SerpApi quota covers Walmart results (1 search per query) and Google Lens (2 per photo, exact + visual).** Worth watching on the free 250/month tier.
- **Thumbnails are upgraded to full resolution.** Every source returns an image sized for its own grid, which looked blurry on larger cards — Amazon 320px, Costco 350px, IKEA's smallest preset, Walmart 576px. All encode the size in the URL, so `app/product_images.py` rewrites it with no extra request: Amazon now 2500px, Temu 1600px, Costco 1200px, Walmart 1000px, IKEA 900px. Unrecognised URLs pass through untouched — a broken high-res guess is worse than a working thumbnail.
- **Truncation is fair across stores.** With results grouped by store, a plain `[:TOP_N]` slice let the first stores take the whole budget: Amazon(40)+Walmart(41)+Temu(19) hit exactly 100 and Costco and IKEA disappeared from a search that had selected them. Slots are now dealt round-robin across stores, then re-grouped for display.
- **Rank Basis is carried on every product.** A site with no best-selling sort is ranked by page order and labelled as such — never presented as a best-seller ranking it didn't earn.
- **Weighted merge.** Before the cross-site sort each Normalized Score is multiplied by a confidence weight for its basis: `sold_count` 1.00, `bestseller_sort` 0.92, `rating` 0.70, `relevance` 0.45. Measured purchase volume outranks a sort position deliberately — "20K+ bought in past month" is a magnitude, while "first in Walmart's best_seller sort" carries none (positions 1 and 2 could differ by 100× or by nothing). Ranking the opinion above the measurement put a Walmart row with no demand data ahead of a 20,000-unit-a-month best seller.
- **Variant rows are collapsed within a site.** Amazon lists every colour and size separately with its own ASIN, which stacked three identical Owala entries at the top. The parent product is identified by shared review count *plus* the brand/model words in the title — both are required, since review count alone can collide and a title prefix alone would merge different products of the same brand. Cross-site merging stays identifier-only.
- **`productList` returns no rating or review data** for any of these five sites — measured, not assumed. That's why the old `rating × review_count` Popularity Score was replaced: it had nothing to compute from. Sold counts (Temu cards, Amazon's "N bought in past month") are the real demand signal. Amazon and Walmart now get ratings from SerpApi directly.
- **Badge chrome is stripped from titles** on the Zyte fallback path. Walmart's `name` arrives as `"100+ bought since yesterday TAL 40 oz…"`; the prefix is peeled off — keeping the number as a demand figure — so titles stay clean for export, compare and $/oz parsing. SerpApi returns titles already clean.
- **Zyte-banned sites go through the browser** (`app/retail_browser.py`): DOM harvest of every product-shaped anchor plus its card text, then regex for price/sold/rating. Class names on Temu and Costco are hashed and rotate; link shape and card text don't.
- **Cross-site merge is identifier-only.** Two listings collapse into one row only with a confirmed shared identifier — never fuzzy title/image matching. Amazon now carries an ASIN and Walmart a `us_item_id` on every row, but those are site-private namespaces, so in practice merging still seldom fires; the conservative rule stands.
- **Caching.** In-memory per (query, sites) for 30 min.

## Category-manager workbench

Daily-driver productivity layer on top of Product Search, built for the "find a winning product" workflow. All persistence is per-browser localStorage — consistent with the app's no-database design.

- **Photo search (Google Lens)** — the camera icon inside the search bar (no separate mode toggle) uploads a photo, crops to the item, and runs a **real reverse-image search via the Google Lens API** (unlike text search, this is never mocked — it calls the backend `/api/trending/search-lens` Apify actor, so it needs the backend running with `APIFY_TOKEN` set). Each hit's destination URL is mapped to its retail site; the pixel-identical ("exact-match") hits on the *selected* sites are shown first as green "Exact match" cards, re-badged to the site they live on. When none of the selected sites have an exact match, it falls back to the 5 closest visual matches with an amber banner. Real Lens data is often sparse (no rating/price), so score chips and $/oz simply don't render for those cards.
- **Market snapshot** — collapsible stats panel above results: median price, price range + distribution histogram, avg rating, total reviews, median $/oz, site mix, and a competition level derived from median review depth.
- **Refine & sort toolbar** — filter within results (text, price band, min rating, min reviews) and sort by opportunity score, price, rating, reviews, or $/oz; shows "X / Y" and one-click clear. Filtering never changes a product's Opportunity Score (scores normalize against the *full* result set).
- **Opportunity Score** — 0–100 chip on each card (demand/quality/value breakdown in the tooltip); unscoreable listings (no rating + no reviews) show nothing rather than a fake 50. See `CONTEXT.md`.
- **$/oz normalization** — capacity and pack count are parsed out of listing titles ("40oz", "700ml", "2 Quart", "2-Pack", "Set of 16") to compare price per fluid ounce per unit; unparseable titles show no figure.
- **Pipeline** (nav tab, with live count badge) — kanban board of saved products across Researching → Contacted → Sampling → Approved / Dropped, with drag-and-drop, per-item notes, tags, target cost, "days ago", Excel export, and a margin button. Products are saved from any result card's bookmark button.
- **Margin calculator** — landed-cost model per product: sell price, unit cost, freight, duty %, channel fee %, fulfillment → landed cost, net margin $/%, ROI, breakeven, and a health verdict. Assumptions persist ("Save as my defaults") and also drive the **Est. margin column** on every supplier row in manufacturer results (retail price vs supplier unit cost).
- **Compare** — pick up to 4 products from result cards; a bottom tray opens a side-by-side table (price, $/oz, rating, reviews, opportunity, source) with best-in-row highlighting.
- **Saved & recent searches** — chips under the search form; star the current query to keep it. Also runnable from the command palette.
- **Command palette** — ⌘K / Ctrl+K: jump between tabs and re-run saved/recent searches from anywhere.

Implementation: pure analytics live in `frontend/src/productMetrics.js` (price/unit parsing, scores, snapshot stats, margin math), persistence hooks in `frontend/src/store.js` (`useShortlist`, `useSavedSearches`, `useRecentSearches`, `useMarginAssumptions` — localStorage + same-page sync events). Each workbench component (`MarketSnapshot`, `ResultsToolbar`, `PipelineView`, `CompareTray`/`CompareModal`, `MarginCalculator`, `CommandPalette`, `SavedSearches`) imports its own co-located CSS; all strings are in `i18n.jsx` (EN + AZ), and everything follows the existing light/dark CSS-variable theming.

## Known limitations

- **Image search is best-effort on all three sites**, with real differences in reliability observed during testing:
  - **AliExpress**: same Baxia/Akamai anti-bot as Alibaba (equally aggressive); succeeded roughly 1 in 3 attempts in testing.
  - **Made-in-China**: much lighter anti-bot (no captcha observed), but has *two* different upload UI variants (a classic `/img-search/` results page, and a newer chat-style "AI mode" results page) — only the classic variant is currently parsed; landing on the AI-mode variant is treated as a failed attempt and retried.
  - **Alibaba**: most fragile of the three — even Zyte's own proxy gets challenged with a slider captcha on a large fraction of requests.
  - In all cases the UI surfaces this honestly via a per-site warning message (e.g. `[Alibaba] ...`) rather than silently showing an empty grid.
- **Seller name/contact varies by site, and this is a genuine data-availability difference, not a bug**:
  - Alibaba & Made-in-China show company name + a "Contact Supplier"/"Send Inquiry" form link in search results.
  - AliExpress's search-results data (confirmed by directly inspecting the embedded JSON) simply doesn't include a seller/store name — it only appears on the individual product page — so AliExpress results show "Unknown seller" with a link to the product page itself.
  - No raw phone/email/WhatsApp was found publicly on any of the three sites' search results; `contact_type` is `"form"` everywhere today. `Product`/`ProductCard` already support a `"direct"` contact type (renders `mailto:`/`tel:`) if a future site or page exposes one.
- Product thumbnail images occasionally fail to load (upstream CDN URLs are sometimes short-lived or session-tied) — cosmetic, not a data-correctness issue.
- Made-in-China's search endpoints are listed in that site's `robots.txt`; worth being aware of for any production/compliance decision, even though they're technically reachable.

## Project layout

```
backend/app/
  main.py                    FastAPI routes, multi-site fan-out (asyncio.gather) + per-site warning prefixing
  zyte_client.py              Zyte API wrapper (browserHtml + httpResponseBody modes)
  scrapers/
    base.py                    Scraper interface (search_by_text / search_by_image)
    alibaba.py, aliexpress.py, made_in_china.py
  parsing/
    alibaba_parser.py, aliexpress_parser.py, made_in_china_parser.py
  models.py                  Product (site, detected_item, inspiration_image_url) / SearchResponse / Trending models
  pinterest.py               Apify client (Idea -> inspiration images)
  detection.py                In-process YOLO-World object detection
  crop.py                    Pillow box-crop + resize + encode
  trending.py                 /api/trending/* routes + in-memory crop store
backend/tests/                Parser + crop unit tests against saved fixtures (no live calls)
frontend/src/
  App.jsx                    Tabs (Product Search / Trending / Pipeline), palette wiring
  sites.js                   Site labels/colors shared across components
  productMetrics.js          Pure analytics: price/unit parsing, opportunity score,
                             market snapshot stats, landed-cost margin math
  store.js                   localStorage hooks: pipeline, saved/recent searches,
                             margin assumptions (+ same-page sync events)
  components/
    SearchBar, SiteFilter, ResultsGrid, ProductCard (site + provenance badges)
    TrendingForm, InspirationGrid, DetectedItemsPanel, TrendingView
    MarketSnapshot, ResultsToolbar, PipelineView, CompareTray/CompareModal,
    MarginCalculator, CommandPalette, SavedSearches (workbench, each w/ own .css)
```
