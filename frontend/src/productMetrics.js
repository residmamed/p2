// Pure product-analytics helpers for the category-manager workbench.
// No React, no side effects — everything takes plain product objects
// (the backend `Product` shape) and returns numbers/objects, so it's trivially
// testable and shareable between cards, toolbars and the pipeline.

// First number in a price string, converted to USD (¥ at a rough 7.2 rate).
// "$5.98" -> 5.98, "¥6.5 - ¥9.0" -> 0.90, "$1.20 - $1.80" -> 1.20.
export function parsePrice(priceText) {
  if (!priceText) return null;
  const text = String(priceText).replace(/,/g, "");
  const m = /([\d.]+)/.exec(text);
  if (!m) return null;
  const v = parseFloat(m[1]);
  if (!Number.isFinite(v)) return null;
  return text.includes("¥") ? v / 7.2 : v;
}

function median(nums) {
  if (!nums.length) return null;
  const s = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

// One-pass market stats over a result set — the "should I even enter this
// niche?" panel. Histogram buckets are computed from the observed price range.
export function marketSnapshot(products) {
  const list = products || [];
  const prices = list.map((p) => parsePrice(p.price_text)).filter((v) => v != null);
  const ratings = list.map((p) => p.rating).filter((v) => v != null);
  const reviews = list.map((p) => p.review_count).filter((v) => v != null);

  const totalReviews = reviews.reduce((a, b) => a + b, 0);
  const medianReviews = median(reviews);

  // Review depth is the moat signal: deep review counts mean entrenched
  // incumbents; shallow ones mean a niche still open to new entrants.
  let competition = "low";
  if (medianReviews != null && medianReviews > 3000) competition = "high";
  else if (medianReviews != null && medianReviews > 800) competition = "medium";

  const BUCKETS = 8;
  let histogram = [];
  if (prices.length) {
    const lo = Math.min(...prices);
    const hi = Math.max(...prices);
    const span = hi - lo || 1;
    histogram = Array.from({ length: BUCKETS }, (_, i) => ({
      lo: lo + (span / BUCKETS) * i,
      hi: lo + (span / BUCKETS) * (i + 1),
      count: 0,
    }));
    for (const p of prices) {
      const idx = Math.min(BUCKETS - 1, Math.floor(((p - lo) / span) * BUCKETS));
      histogram[idx].count += 1;
    }
  }

  const siteCounts = {};
  for (const p of list) siteCounts[p.site] = (siteCounts[p.site] || 0) + 1;
  const siteMix = Object.entries(siteCounts)
    .map(([site, count]) => ({ site, count, pct: list.length ? count / list.length : 0 }))
    .sort((a, b) => b.count - a.count);

  return {
    count: list.length,
    pricedCount: prices.length,
    minPrice: prices.length ? Math.min(...prices) : null,
    maxPrice: prices.length ? Math.max(...prices) : null,
    medianPrice: median(prices),
    avgPrice: prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : null,
    avgRating: ratings.length ? ratings.reduce((a, b) => a + b, 0) / ratings.length : null,
    totalReviews,
    medianReviews,
    competition,
    histogram,
    siteMix,
  };
}

// Ranking by the star value alone is a lie the review count can expose: a lone
// 5.0 from two buyers outranks a 4.7 backed by twenty thousand, so the grid
// leads with exactly the listings we know least about. What the sort should
// answer is not "who has the highest average?" but "whose rating would still
// hold up if more people bought it?"
//
// That's the lower bound of a confidence interval on the rating, scored with
// the Wilson interval. The star average is read as a share of a perfect score
// (4.7 of 5 -> p = 0.925), and the sort ranks the *pessimistic* end of the
// interval around it: the more reviews behind a rating, the tighter the
// interval and the less it is marked down.
//
//   4.7 x 21,000 reviews  ->  scores just under 4.7   (nothing left to doubt)
//   5.0 x 2 reviews       ->  scores far below it     (two buyers prove little)
//
// The Bayesian/IMDb shrinkage toward a set average was tried first and is the
// wrong instrument for this grid: when the set's mean rating lands near the
// best-evidenced listing's own rating, every score collapses into a near-tie
// and the unproven 5.0 edges ahead on upside — the original complaint, intact.
// A confidence bound has no such degenerate case; it rises monotonically with
// review count, so more evidence can only ever help a listing.
const Z = 1.96; // 95% confidence
const RATING_SCALE = 5;

// Wilson lower bound for a rating, in the original star units so the number
// stays legible next to the rating it came from. null for listings with no
// rating at all — no evidence in either direction, left for the caller to place.
export function ratingConfidenceScore(product) {
  const rating = product.rating;
  if (rating == null) return null;

  const n = product.review_count != null && product.review_count > 0 ? product.review_count : 0;
  // A rating published with no review count behind it is an unsupported claim.
  // Scored at the floor rather than dropped: it stays in the grid, below every
  // listing that can show its work, and above the entirely unrated.
  if (n === 0) return 0;

  const p = Math.min(1, Math.max(0, rating / RATING_SCALE));
  const z2 = Z * Z;
  const lower =
    (p + z2 / (2 * n) - Z * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n))) / (1 + z2 / n);
  return lower * RATING_SCALE;
}

export function formatUSD(v, { compact = false } = {}) {
  if (v == null || !Number.isFinite(v)) return "—";
  if (compact && Math.abs(v) >= 1000) return `$${(v / 1000).toFixed(1)}k`;
  return `$${v.toFixed(2)}`;
}
