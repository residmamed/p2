# Best Seller Search: server-side Zyte, cached, identifier-only merge

## Context

We added a Best Seller Search mode: a user submits a keyword and gets one merged
top-100 ranking of the best-selling matches across seven consumer-retail sites
(Amazon, Walmart, Temu, Costco, B&M, Wibra, IKEA). None of these sites expose the
same popularity data, most have no query-scoped "best selling" sort, and several
have no public API. We had to decide how to fetch, rank, merge, and serve.

## Decision

1. **Fetch server-side via Zyte's automatic `productList` extraction**, not a
   browser extension and not seven hand-written parsers. Zyte's ML extraction
   returns uniform structured listings (name, price, rating, review count, image,
   url) off arbitrary search pages and adapts to DOM changes, which is what makes
   this "dynamic and accurate" across sites we've never parsed.
2. **Rank by ordinal Site Rank where a best-selling sort exists (today only
   Walmart), else by a Popularity Score = rating × review_count.** Each signal is
   min-max normalized *within a site's own results* to a 0–1 Normalized Score so
   ordinal and review-weighted signals are comparable across sites. Combined Rank
   is the sort of the merged pool by Normalized Score.
3. **Merge listings across sites only on a confirmed Shared Identifier
   (GTIN/MPN/etc.).** No fuzzy title/image matching. `productList` rarely exposes
   an identifier, so merging seldom fires and listings usually stay separate.
4. **Serve from a short-TTL (30 min), in-memory, query-keyed Result Cache.** No
   database (consistent with the rest of the app).

## Considered options

- **Browser extension (like the Google Lens bridge):** rejected — it only runs
  while the user's own Chrome is open, can't serve a multi-user backend, and would
  drive 7 sites' pagination sequentially in one tab. The Lens bridge exists to
  defeat Google's *automation blocking*, a problem we don't have here.
- **Fuzzy cross-site product matching:** rejected — merging two genuinely
  different products into one row is a worse accuracy failure than showing the
  same product twice, and the user prioritized accuracy. A false merge would
  conflate rank/price of distinct items.
- **Round-robin interleaving of per-site lists:** rejected — treats a site's weak
  #1 the same as another site's dominant #1. Normalized Score preserves the gap.

## Consequences

- Only Walmart currently contributes true Site Rank; the other six rank on
  Popularity Score (or, if a site returns no rating data at all, page order, with
  a warning). This is honest to the data these sites actually expose today.
- Because Shared Identifiers are usually absent, the top-100 will often contain
  the same popular product listed separately per site rather than merged.
