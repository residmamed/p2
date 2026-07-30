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

## Lens sourcing: the same question, in seconds, with no browser

`POST /api/find-suppliers` (`app/lens_suppliers.py`) is a second route to "who manufactures this?" built on two plain REST calls and nothing else. It does not replace the pipeline above — it finds less — but it answers while the user is still looking at the screen.

```
STEP 1  SerpApi Google Lens    photo -> product URLs across the whole web
STEP 2  Oxylabs Web Scraper    each Alibaba/1688 URL -> supplier, price, MOQ
```

```jsonc
POST /api/find-suppliers   { "image_url": "..." }   // or { "image_base64": "..." }
{
  "query_image": "https://n.uguu.se/uLcAVoUa.jpg",
  "results": [ { "supplier_name": …, "price": {"min":1.2,"max":2.5,"currency":"USD"},
                 "moq": "100 pieces", "source": "alibaba",
                 "match_confidence": "lens_exact_match", "enriched": true } ],
  "partial_matches": [ … ],          // non-marketplace Lens hits, for context
  "latency_ms": 5001,
  "step_timings": { "lens_ms": 4115, "enrichment_ms": 0, "upload_ms": 881 }
}
```

**No browser, no Apify.** Both vendors are REST. Uploaded bytes are published first via `serp_lens._publish`, because SerpApi's Lens endpoint accepts a public URL and nothing else — no upload, no multipart, no base64.

**Oxylabs source names** (`app/oxylabs_client.py`), from the docs on 2026-07-29: `alibaba` takes any Alibaba URL; `alibaba_product` takes a product *id*; 1688 and Taobao have no dedicated target and go through `universal`. All of them return **HTML, not parsed JSON** — Alibaba isn't a domain `parse: true` covers — so the structured schema is built by `app/parsing/marketplace_product.py`, which reads og: tags, then JSON-LD, then the page's own hydration blob, then visible text, taking the first source that answers each field.

**Measured, on this machine.**

| run | total | upload | lens | outcome |
|---|---|---|---|---|
| dinnerware set | 4673ms | 843ms | 3829ms | 60 Lens matches, 0 reachable on a marketplace |
| travel tumbler | 5001ms | 881ms | 4115ms | **1 Alibaba listing**, 163 context matches |
| stroller | 3774ms | 447ms | 3323ms | 94 matches, 0 on a marketplace |
| any, second time | **~1ms** | — | ~1ms | served from the 30-day cache |

Two things that table says plainly:

- **The 1–2s image-match target is not achievable with this vendor.** SerpApi Lens took 3.3–4.1s on every cold run, and one photo blew the 5s step-1 cap outright and returned a clean `502 Google Lens did not answer within 5s.` The 5s end-to-end budget survives only because step 2 is concurrent — it is step 1 that spends it.
- **Lens's coverage of Chinese B2B listings is thin.** One marketplace hit across three consumer products. This is the honest cost of not searching the marketplaces' own image indexes, and it is why `/api/sourcing/by-url` still exists and why an empty `results` here ships with a warning saying "Lens found nothing on these sites", never a bare empty list.

**`exact_matches` links are mostly unusable, and that had to be handled rather than ignored.** Every row of a `type=exact_matches` response arrives as `lens.google.com/goto?url=<token>`. The token base64-decodes to protobuf-framed ciphertext with no plaintext URL in it, and fetching the wrapper server-side answers **404** even with a browser user-agent — so the destination is genuinely unknowable without opening it in a browser, the one thing this pipeline exists to avoid. Those rows go to `partial_matches` with a warning naming the marketplace and the count, never to `results`: a supplier row nobody can open, whose supplier can never be read, is worse than an honest absence. Not all of them are wrapped — a live run returned `arabic.alibaba.com/product-detail/…` directly, and that one enriches normally. Fixing this also fixed a dedupe bug it hid: the canonical form drops query strings, and every wrapper shares the path `/goto`, so all 41 exact matches would otherwise have collapsed into one.

**Caching.** Step 1 only, 30 days, keyed on SHA256 of the image bytes for an upload and on the URL for a URL. Step 2 is deliberately never cached: a month-old candidate URL is fine, a month-old price is not. The lookup happens *before* the upload — ordering it the other way cost a full 761ms image publish on a 764ms cache hit, i.e. the entire saving handed straight back.

**Degradation, per the same rule as the rest of the app.** No Oxylabs credentials → every row survives on SerpApi's inline title/price/thumbnail, flagged `enriched: false` with the reason on the row. Credentials *rejected* → the same, plus a top-level `errors` entry naming the env vars, kept separate from `warnings` because it is an operator's problem and not a partial result. One product page hangs past its 8s → that row alone falls back. A page that loads but parses to nothing is reported as such rather than yielding a half-invented supplier. `supplier_name` is never back-filled from Lens's `source` label — that string is `"Alibaba.com"` on every Alibaba row, so using it would print the marketplace where the factory's name goes.

**Step 2, verified live against Alibaba** (2026-07-29, real Oxylabs credentials). A batch of 7 URLs enriched 6, in 8.6s wall clock; the seventh was a delisted listing and correctly degraded. Suppliers came back correctly *distinct* across product categories (`Shen Zhen Liyonda Technology`, `Shenzhen Tao Hui Industrial`, `Jinhua Danuo Melamine Tableware`), which is what rules out the parser latching onto page chrome. Per-page latency, six fetched concurrently: **2.3 / 2.3 / 2.5 / 2.8 / 3.4 / 4.9s** for 410–454KB pages.

Three things that only a live run could have shown, each now pinned by a test:

**1. JSON-LD publishes the wrong price.** Alibaba's `offers.price` is the *bottom* of the quantity ladder — the rate at five thousand units — while the MOQ is 24:

```
JSON-LD    "price": "2.99", "priceCurrency": "USD"
priceList   $3.99 (24-199)   $3.59 (200-4,999)   $2.99 (5,000+)
MOQ         24 pieces
```

Publishing "$2.99, MOQ 24 pieces" isn't a rounding error, it's the wrong number — at the minimum order this supplier charges $3.99. The ladder wins, and both ends are reported as a range.

**2. Prices are localised by exit IP, and the currency does not travel with them.** The same URL returned its ladder in Serbian dinar (`RSD 452.71`) from one exit and dollars from another, while the JSON-LD block still said `USD`. Taking a number from one source and a currency from the other reports **$452.71 for a $2.99 tumbler**, so every price is parsed out of a single formatted string carrying its own symbol, and a ladder whose rungs disagree about currency is discarded rather than reconciled. (The first version of that money regex knew only `$`/`¥`/`USD`/`CNY`, so a euro page parsed to nothing, the ladder was skipped, and the price silently fell back to the misleading floor — the exact bug the ladder exists to prevent, reintroduced by a regex too narrow to notice.)

The localisation itself is now pinned at the source. Six fetches of one product URL:

```
no geo_location   R 28,52 · 82.47 TL · $1.67 · R 28,52 · R 28,52 · $1.67
"United States"   $1.67 · $1.67 · $1.67 · $1.67 · $1.67 · $1.67
```

So `oxylabs_client.GEO_FOR_SITE` fixes Alibaba's exit to the US and the results table can actually be used to compare suppliers. Pinning beats converting downstream — there is no exchange rate in this codebase and an invented one would put a wrong number on a supplier quote. 1688 and Taobao are left unpinned: they are domestic Chinese sites that quote CNY natively.

**3. Delisted listings serve a complete, well-formed lie.** A dead Alibaba product answers **HTTP 200** with valid JSON-LD: `name: "Product Not Available"`, `brand: "Alibaba"`, `price: "0.99"`, `availability: "InStock"`. Nothing about its shape says it is dead — the first live run of this pipeline duly reported a supplier called "Alibaba" selling a "Product Not Available" for $0.99. It is now caught by title, and the marketplace's own name is never accepted as a supplier.

Alibaba **product** pages are not bot-checked (0 captcha markers across every page fetched); Alibaba **search** pages through the same source are (26 markers), which matches what `supplier_resolve.py` already recorded. Transient `504`s from Oxylabs' own gateway are retried once — observed turning 3-of-4 into 4-of-4 — while timeouts are not, since those have already spent the per-URL budget.

Test script: `python -m scripts.find_suppliers_demo <url|path>` prints timings, warnings and the full JSON (`--no-cache` forces a live call).

> **Still unverified:** the 1688 and Taobao selectors in `marketplace_product.py`. Lens returned no reachable listing on either site during testing, so nothing exercised them; they are written from the page's documented shape and follow the `supplier_resolve.VERIFIED` convention. A miss there costs one field on one row — the caller falls back to SerpApi's data and says so.

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

## Product Search: one merged top-100 across twelve sources

*What's already selling at retail for this keyword?* — across **Amazon, Walmart, Temu, Costco, IKEA, Target, Home Depot, eBay, Etsy, Best Buy and Wayfair**, plus **Google Shopping**, which is not a store and answers the keyword by picture instead. `/api/bestsellers?q=...&sites=...` fans out concurrently and returns one merged, ranked top-100 (`app/bestsellers.py`). **No mock data anywhere** — the frontend calls this endpoint directly.

Every site's capability below was **probed live**, not assumed (`spikes/probe_bestseller_sorts.py`, `spikes/probe_hard_sites.py`):

| Site | Transport | Rank basis | Evidence |
|---|---|---|---|
| Amazon | **Rainforest** + `sort_by=bestseller_rankings` | `sold_count` | The sort **reorders** (Owala and Stanley lead, which are in fact the best sellers). Returns `recent_sales` ("20K+ bought in past month"), **exact** review counts, rating and ASIN. Falls back to **SerpApi** `engine=amazon`, then to Zyte |
| Walmart | **SerpApi** `engine=walmart` + `sort=best_seller` | `bestseller_sort` | The sort **reorders**. Returns a typed `offer_price` (separate from `was_price`), rating, review count and `us_item_id` in **one** request. Falls back to Zyte `productList` + `__NEXT_DATA__` |
| IKEA | Zyte `productList` + product-page JSON-LD | `relevance` | `sort=BEST_SELLER` is **ignored**. Search results carry no ratings, but each product page ships schema.org `aggregateRating` — fetched per product, affordable because IKEA returns only ~4 results |
| Temu | **Apify** `amit123/temu-products-scraper` | `sold_count` | Zyte returns **0 products** in every mode and Browserbase hits a "Security verification" wall. The actor returns `sales_num` ("46K+") — real units sold — plus rating/review count nested inside `comment` (`goods_score`, `comment_num_tips`) |
| Costco | **Apify** `e-commerce/costco-fast-product-scraper` | `rating` | Zyte returns **HTTP 520 Website Ban**. The actor returns `rating` + `reviewsCount` but no sales figures |
| Target | **Apify** `automation-lab/target-scraper` + `sort=bestselling` | `bestseller_sort` | The sort **reorders** — "water bottle" comes back Owala-first, which is in fact the best seller. Returns `rating` + `reviewCount` on 8 of 10 rows. The top row is routinely the one with **no price**: Target prices multi-variant listings per variant |
| Home Depot | **Apify** `crawlerbros/homedepot-scraper` + `sortBy=top_sellers` | `bestseller_sort` | Home Depot's own top-sellers order. **No rating or review count** anywhere in the output, so the sort is the only signal it offers — a stronger one than a rating anyway. `maplerope44/home-depot-product-lookup` was 10× more popular but takes a `productId`, so it cannot answer a keyword search at all |
| eBay | **Apify** `automation-lab/ebay-scraper` + `sort=best_match` | `relevance` | The sort enum offers **no best-selling option**. `soldCount` exists but came back **empty on every probed row**, and the only rating-shaped fields (`sellerFeedbackPercent`) rate the *seller across all sales*, not the product — so eBay rows carry no rating. Restricted to `buy_it_now`: an auction has no stable price to rank or compare |
| Etsy | **Apify** `automation-lab/etsy-scraper` | `relevance` | No best-selling sort. Returns a `rating` with **no review count at all**, so the rating travels on the row but can't order the grid. Its `price` is **malformed** — a $19.99 listing arrives as `"19.9919"`, the price with its own leading digits repeated — recovered by taking the leading amount |
| Best Buy | **Apify** `piotrv1001/bestbuy-listings-scraper` | `relevance` | No sort parameter of any kind. Complete `rating` + `reviewsCount`, price nested under `priceDomain.currentPrice`. `crawlerbros/bestbuy-scraper` was tried first and returned `{"type": "bestbuy_error", "reason": "no_results"}` — its bot challenge had persisted across all retries — so this site is the likeliest of the six to come back empty |
| Wayfair | **Apify** `piotrv1001/wayfair-listings-scraper` | `relevance` | Takes only `startUrls`, so no sort. The most complete data of the six: price, `rating` and `reviewCount` on **10 of 10** probed rows. `mscraper/wayfair-scraper` is the better-known actor but rents at a **flat $20/month**; this one bills $0.0015 per product |
| Google Shopping | **Apify** Pinterest → **SerpApi** Google Lens | `relevance` | Not a store — see below. Nothing in the chain ranks anything, so it carries the lowest confidence weight in the merge |

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
- **Thumbnails are upgraded to full resolution.** Every source returns an image sized for its own grid, which looked blurry on larger cards — Amazon 320px, Costco 350px, IKEA's smallest preset, Walmart 576px. All encode the size in the URL, so `app/product_images.py` rewrites it with no extra request: Amazon now 2500px, Temu 1600px, Costco 1200px, Walmart 1000px, IKEA 900px. Unrecognised URLs pass through untouched — a broken high-res guess is worse than a working thumbnail. The five newer rules were each checked by fetching both URLs, since a size token that *looks* like a dimension is not evidence that it is one: eBay `s-l500`→`s-l1600` (20KB→86KB), Etsy `il_300x300`→`il_1140xN` (12KB→103KB), Home Depot `_600`→`_1000` (17KB→37KB), Wayfair `resize-h600-w600`→`h1600-w1600` (60KB→377KB), and Target — whose actor sends a **bare** Scene7 URL with no size directive at all, rendering ~9KB — gains `?wid=1200` (9KB→90KB).
- **A `0` rating means unrated, not terrible.** Best Buy returns `rating: 0.0` alongside `reviewCount: 0` for a product nobody has reviewed. Every store here rates on 1–5 stars, so there is no such thing as a real zero: kept as `0.0` it would sort below every genuinely bad product and drag the Market Snapshot average down, asserting something about the product the store never said. Both fields are cleared. (Walmart's fallback parser already did this for the same reason.)
- **Truncation is fair across stores.** With results grouped by store, a plain `[:TOP_N]` slice let the first stores take the whole budget: Amazon(40)+Walmart(41)+Temu(19) hit exactly 100 and Costco and IKEA disappeared from a search that had selected them. Slots are now dealt round-robin across stores, then re-grouped for display.
- **Rank Basis is carried on every product.** A site with no best-selling sort is ranked by page order and labelled as such — never presented as a best-seller ranking it didn't earn.
- **Weighted merge.** Before the cross-site sort each Normalized Score is multiplied by a confidence weight for its basis: `sold_count` 1.00, `bestseller_sort` 0.92, `rating` 0.70, `relevance` 0.45. Measured purchase volume outranks a sort position deliberately — "20K+ bought in past month" is a magnitude, while "first in Walmart's best_seller sort" carries none (positions 1 and 2 could differ by 100× or by nothing). Ranking the opinion above the measurement put a Walmart row with no demand data ahead of a 20,000-unit-a-month best seller.
- **Variant rows are collapsed within a site.** Amazon lists every colour and size separately with its own ASIN, which stacked three identical Owala entries at the top. The parent product is identified by shared review count *plus* the brand/model words in the title — both are required, since review count alone can collide and a title prefix alone would merge different products of the same brand. Cross-site merging stays identifier-only.
- **`productList` returns no rating or review data** for any of the five sites it serves — measured, not assumed. That's why the old `rating × review_count` Popularity Score was replaced: it had nothing to compute from. Sold counts (Temu cards, Amazon's "N bought in past month") are the real demand signal. Amazon and Walmart now get ratings from SerpApi directly.
- **Badge chrome is stripped from titles** on the Zyte fallback path. Walmart's `name` arrives as `"100+ bought since yesterday TAL 40 oz…"`; the prefix is peeled off — keeping the number as a demand figure — so titles stay clean for export, compare and $/oz parsing. SerpApi returns titles already clean.
- **Zyte-banned sites go through the browser** (`app/retail_browser.py`): DOM harvest of every product-shaped anchor plus its card text, then regex for price/sold/rating. Class names on Temu and Costco are hashed and rotate; link shape and card text don't.
- **Cross-site merge is identifier-only.** Two listings collapse into one row only with a confirmed shared identifier — never fuzzy title/image matching. Amazon now carries an ASIN and Walmart a `us_item_id` on every row, but those are site-private namespaces, so in practice merging still seldom fires; the conservative rule stands.
- **Google Shopping answers the keyword by picture** (`app/google_shopping.py`). Every other source hands the keyword to a store's search box; this one goes `keyword → Pinterest images → Google Lens on each → the pages Lens found selling that thing → keep only the titles that describe the keyword`. Pinterest is the entry point because its images are what people actually save about a category, and because Lens needs a publicly reachable URL — which a Pinterest CDN image already is, so nothing has to be uploaded (see `serp_lens._publish` for why that matters).

  **The last step is the whole difficulty, and it was measured.** A Pinterest image for "desk lamp" is a photograph of a *room*, and Lens answers with what it sees in it: the desk, the chair, the rug, the plant. Each is a real product on a real shop page and each is the wrong answer. The gate requires two things of a title — the keyword's **head noun** (the last word of an English product phrase is the product) and enough of the qualifiers. Both are needed, and a live "desk lamp" run showed why:

  | gate | dropped | what got through |
  |---|---|---|
  | 50% word overlap only | 55 of 231 | lamp shades, ceiling lights, floor lamps — every one contains "lamp" |
  | head noun + **both** words of a 2-word keyword | **182 of 231** | desk lamps from Walmart, Wayfair, Etsy, eBay, Amazon |

  Without the head-noun rule, `"Stainless Steel Cutlery Set"` scores two of four against `"stainless steel water bottle"` and clears a 50% threshold while being a different product. Without the two-word floor, a two-word keyword is satisfied by its head noun alone and `"desk"` is never actually required. The gate lives in this module rather than relying on the Claude relevance screen in `bestsellers.py`, because that screen is a kill switch away from being off and this source is unusable without filtering. Pinterest's own domains are blocked from the results — the chain starts there, so a Pinterest board Lens matched back to the image we searched with is a circle, not a shop. Priced listings sort first: on a source called Shopping, a page you can buy from shouldn't sit below a blog post about the same lamp (only a minority of Lens rows carry a price at all).

  **Cost:** one Apify actor run plus **2 SerpApi credits per image** (exact + visual are separate calls) — 8 per search at the default 4 images, against the quota shared with Amazon, Walmart and the photo search. Which is why the image count is small and the source is opt-in rather than in the default set.
- **"Find more" is per store, not global** (`/api/bestsellers/more?q=&site=&have=`). One button per store that returned results, because "more" means something different at each one and only the store can say whether it has any: Target can open another page of its best-selling sort, while IKEA returns nine results in total and is simply finished. A single global button would have to average those into one answer and be wrong at both ends. An empty batch is how a store says it's done — the button says **"no more"** rather than raising an error, since being finished is not a fault. Two of the three transports can go deeper: actors are asked for `have + 24` rows and the tail is returned (none of them accepts an offset, so the earlier rows are billed again — which is exactly why this is a button and not automatic), and SerpApi walks the same sort by page. Rainforest is skipped on this path because it has no page parameter and would re-return page 1 as though it were page 2. Depth is capped at 150: every press refetches everything before it, so cost grows while the number of new rows stays flat. A photo search shows no buttons at all — its results came from Lens matching an image, so there is no keyword to page with.
- **Caching.** In-memory per (query, sites) for 30 min. The per-store "find more" is deliberately uncached — it exists to go past what the cached first page holds.

## Category-manager workbench

Daily-driver productivity layer on top of Product Search, built for the "find a winning product" workflow. All persistence is per-browser localStorage — consistent with the app's no-database design.

- **Photo search (Google Lens)** — the camera icon inside the search bar (no separate mode toggle) uploads a photo, crops to the item, and runs a **real reverse-image search via the Google Lens API** (unlike text search, this is never mocked — it calls the backend `/api/trending/search-lens` Apify actor, so it needs the backend running with `APIFY_TOKEN` set). Each hit's destination URL is mapped to its retail site; the pixel-identical ("exact-match") hits on the *selected* sites are shown first as green "Exact match" cards, re-badged to the site they live on. When none of the selected sites have an exact match, it falls back to the 5 closest visual matches with an amber banner. Real Lens data is often sparse (no rating/price), so score chips and $/oz simply don't render for those cards.
- **Market snapshot** — collapsible stats panel above results: median price, price range + distribution histogram, avg rating, total reviews, median $/oz, site mix, and a competition level derived from median review depth.
- **Refine & sort toolbar** — filter within results (text, price band, min rating, min reviews) and sort by opportunity score, price, rating, reviews, or $/oz; shows "X / Y" and one-click clear. Filtering never changes a product's Opportunity Score (scores normalize against the *full* result set).
- **Sorting by rating weighs the review count.** Raw stars put a 5.0 from two buyers above a 4.7 from twenty-one thousand, so the grid led with the listings we knew least about. The sort ranks the **lower bound of a Wilson confidence interval** on the rating instead: the more reviews behind a rating, the less it is marked down. `4.7 × 21,000` scores just under 4.7; `5.0 × 2` scores far below it. A Bayesian/IMDb shrinkage toward the set average was tried first and is the wrong instrument here — when the set's mean lands near the best-evidenced listing's own rating, every score collapses into a near-tie and the unproven 5.0 edges ahead on upside, which is the original complaint intact. A confidence bound has no such degenerate case and rises monotonically with review count, so more evidence can only ever help. A rating published with no review count sits at the floor rather than being dropped, and a `0` rating is treated as unrated (see above). The dropdown says "Rating (review-weighted)", because it no longer means raw stars.
- **Suppliers are prefetched silently after a product search — both passes.** When results land, the supplier lookup starts in the background for the top products and renders nothing: no progress bar, no rows, no warnings. Suppliers still appear only when the user asks for them; the point is purely that the asking is then fast. The caches in `api.js` are keyed per product photo, so the click either finds the answer already there or joins the request in flight rather than re-issuing (and re-billing) it. The prefetch is capped, runs on its own AbortController so a new search cancels it, and is cleared when a new search starts so a previous query can never serve its suppliers.
  - **The prefetch follows through to the deep marketplace search**, which it originally skipped as too expensive to run unasked. Skipping it warmed the cheap half of the work: Google Lens has no Chinese B2B listing for most branded retail products, so the click still had minutes of driven browsers ahead of it and a progress bar reading "searching the marketplaces directly (slower)". That wait was the prefetch's whole reason for existing, unaddressed. The deep pass now starts with the product results.
  - **It is paced, not fanned out.** One deep search already drives three browser sessions at once (`sourcing.py`'s `DISCOVERY_CONCURRENCY`), so twelve concurrently would be thirty-six and Browserbase would refuse most of them. Products go through the deep pass one at a time, and only the first `AUTO_DEEP_MAX` of them — the unasked-for run never holds more sessions than one press of the button does.
  - **The slow-search message is only shown for products actually being waited on.** The deep cache tracks whether each lookup has come back, not just whether it was started, so a click landing after the prefetch finished gets its suppliers straight out of the cache with no "searching the marketplaces" claim attached. A click landing mid-prefetch still shows it, because then it is true.
- **Supplier phone numbers are not displayed.** Not in the results table (the column is gone), not in the message modal's recipient chips (they name the channel — "WhatsApp", "SMS" — instead of the number), and not in the Excel export, since an export that carried them would put back on a spreadsheet exactly what was taken off the screen. Email and the platform inbox are the contact routes offered. The backend still *collects* phone numbers, which is what makes the WhatsApp/SMS channels available at all.
- **Opportunity Score** — 0–100 chip on each card with the demand/quality/value bars beneath it, plus the confidence-adjusted rating, price vs the set median, and the listing's [[rank-basis]]. Shown under the product name behind a **Show metrics** toggle (top right, beside Export), which persists per browser. Scores normalize against the *full* result set, so filtering the grid never restates them. Unscoreable listings (no rating **and** no reviews) say so rather than showing a fake 50. See `CONTEXT.md`.
- **$/oz normalization** — capacity and pack count are parsed out of listing titles ("40oz", "700ml", "2 Quart", "2-Pack", "Set of 16") to compare price per fluid ounce per unit; unparseable titles show no figure.
- **Pipeline** — *not implemented.* Designed as a nav tab with a live count badge: a kanban board of saved products across Researching → Contacted → Sampling → Approved / Dropped, with drag-and-drop, per-item notes, tags, target cost, Excel export, saved from a bookmark button on each card. No part of it exists yet — there is no `useShortlist` hook, no `PipelineView`, and no bookmark control.
- **Margin calculator** — *not implemented.* Designed as a landed-cost model per product (sell price, unit cost, freight, duty %, channel fee %, fulfillment → net margin $/%, ROI, breakeven) with persisted assumptions driving an Est. margin column on supplier rows. None of the math, the `useMarginAssumptions` hook, or the component exists yet.
- **Compare** — *not implemented.* Designed as a bottom tray comparing up to 4 picked products side by side. No `CompareTray`/`CompareModal` exists.
- **Saved & recent searches** — chips under the search form; star the current query to keep it. Also runnable from the command palette.
- **Command palette** — ⌘K / Ctrl+K: jump between tabs and re-run saved/recent searches from anywhere.

Implementation: pure analytics live in `frontend/src/productMetrics.js` (`parsePrice`, `parseVolumeOz`/`parsePackCount`/`pricePerOz`, `marketSnapshot`, `ratingConfidenceScore`, `opportunityScores`), persistence hooks in `frontend/src/store.js` (`useStoredState`, `useSavedSearches`, `useRecentSearches` — localStorage + same-page sync events). Each workbench component (`MarketSnapshot`, `ResultsToolbar`, `ProductMetrics`, `CommandPalette`, `SavedSearches`) imports its own co-located CSS; all strings are in `i18n.jsx` (EN + AZ). `productMetrics.js` is covered by `productMetrics.test.js` — run with `npm test` (vitest).

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
  lens_suppliers.py           /api/find-suppliers — the browserless SerpApi Lens + Oxylabs pipeline
  oxylabs_client.py           Oxylabs Web Scraper API (realtime endpoint, typed auth vs transient failures)
  lens_cache.py               30-day disk cache of step-1 Lens results, keyed by image hash
  scrapers/
    base.py                    Scraper interface (search_by_text / search_by_image)
    alibaba.py, aliexpress.py, made_in_china.py
  parsing/
    alibaba_parser.py, aliexpress_parser.py, made_in_china_parser.py
    marketplace_product.py     Alibaba/1688/Taobao product page -> og: / JSON-LD / hydration blob / text
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
