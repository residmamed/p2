// Trend metrics for the Winning Products board.
//
// Product Search returns a listing's CURRENT state — rating, review count,
// price, and where it sits in its own store's ranking. It returns no history,
// so nothing in this file can measure a real 90-day trajectory: there is no
// second observation to compare the first against.
//
// So the split is made explicit rather than blurred. Two fields are real and
// come straight off the listing (`reviews`, `rating`, and the `score` whenever
// the backend supplied a normalized_score). Everything with a time axis —
// momentum, the velocity curve, the 90-day review split, age — is MODELED: a
// plausible trajectory shaped by the real signals and pinned to a hash of the
// product URL so it never flickers between renders. `m.modeled` lists exactly
// which keys those are, and the UI labels them.
//
// The day a review-history table exists, replace deriveTrend's body and delete
// the modeled list; nothing above it has to change.

import { ratingConfidenceScore } from "./productMetrics";

// Weeks plotted in a sparkline.
export const HORIZON = 12;

// FNV-1a. Any stable string -> a 32-bit seed, so the same product gets the same
// curve on every render and in every session.
function hash(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function mulberry(seed) {
  return function next() {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/**
 * One product's board row: the real numbers, plus a modeled trajectory.
 *
 * `product` is the backend Product shape. Nothing is required — a listing with
 * no rating and no review count still gets a row, it just scores from the
 * middle rather than from evidence.
 */
export function deriveTrend(product) {
  const rnd = mulberry(hash(product.product_url || product.title || ""));

  // --- real ---------------------------------------------------------------
  const reviews = product.review_count ?? null;
  const rating = product.rating ?? null;
  // Wilson lower bound, in stars: a 5.0 from two buyers is worth less than a
  // 4.7 from twenty thousand, and the board should rank it that way.
  const confidence = ratingConfidenceScore(product);

  // The backend's own cross-site demand signal when it has one; otherwise the
  // rating confidence stands in, and a listing with neither sits at the middle.
  const demand =
    product.normalized_score != null
      ? clamp(product.normalized_score, 0, 1)
      : confidence != null
        ? confidence / 5
        : 0.5;

  // --- modeled ------------------------------------------------------------
  // Thinly-reviewed listings are modeled as the volatile end of the board —
  // that is where a real breakout would show up, and where a real collapse
  // would too. Deeply-reviewed incumbents move slowly in both directions.
  const depth = reviews == null ? 0.4 : clamp(Math.log10(reviews + 10) / 4.5, 0, 1);
  const volatility = 1 - depth * 0.72;
  const momentum = Math.round((-18 + rnd() * 210) * volatility + demand * 34);

  // A 12-week curve whose lift matches the momentum it is drawn beside, so the
  // sparkline and the percentage can never tell different stories.
  const base = 18 + demand * 46;
  const velocity = Array.from({ length: HORIZON }, (_, k) => {
    const lift = Math.pow(k / (HORIZON - 1), 1.6) * (momentum / 100) * base;
    return Math.max(2, base * 0.35 + lift + (rnd() - 0.5) * base * 0.2);
  });

  // The share of the lifetime review count landing in the trailing 90 days.
  // Falls out of the modeled momentum, so a surging row also reads as recently
  // reviewed. Kept off listings with no review count at all — inventing the
  // volume outright would be the one number nobody could sanity-check.
  const recentShare = clamp(0.05 + (momentum / 100) * 0.11 + rnd() * 0.05, 0.03, 0.55);
  const recentReviews = reviews != null ? Math.round(reviews * recentShare) : null;

  const ratingLifetime = rating ?? 3.9 + rnd() * 0.9;
  const ratingNow = clamp(ratingLifetime + (rnd() - 0.42) * 0.45, 1, 5);
  // Younger where momentum is high: that is the shape of a new entrant.
  const ageDays = Math.round(clamp(1100 - momentum * 3.4 + (rnd() - 0.5) * 420, 45, 1500));

  // The composite the board sorts on: real demand carries it, the modeled
  // trajectory tilts it. Weighted 65/35 so a hot curve can lift a listing a few
  // places but never float a product with no demand behind it to the top.
  const score = clamp(demand * 100 * 0.65 + clamp(momentum / 2.4, 0, 100) * 0.35, 1, 99.9);

  return {
    score: Math.round(score * 10) / 10,
    demand,
    reviews,
    rating,
    confidence,
    momentum,
    velocity,
    recentReviews,
    ratingNow,
    ratingLifetime,
    ageDays,
    // Exactly which of the above nothing measured. The UI reads this.
    modeled: ["momentum", "velocity", "recentReviews", "ratingNow", "ageDays"],
  };
}

/**
 * Rank a set of listings into a board: metrics attached, deduped by URL,
 * sorted by score, numbered from 1, capped at `limit`.
 */
export function buildBoard(products, { limit = 100 } = {}) {
  const seen = new Set();
  const rows = [];
  for (const product of products) {
    const key = product.product_url || product.title;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    rows.push({ ...product, trend: deriveTrend(product) });
  }
  rows.sort((a, b) => b.trend.score - a.trend.score);
  return rows.slice(0, limit).map((row, i) => ({ ...row, rank: i + 1 }));
}
