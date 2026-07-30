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

  // Unit price, over only the listings whose title actually stated a size.
  // `perOzCount` travels with it so the tile can say what it is based on — a
  // median over 3 of 42 listings is a different claim from one over all 42.
  const perOzValues = list.map((p) => pricePerOz(p)).filter((v) => v != null);

  return {
    count: list.length,
    pricedCount: prices.length,
    medianPerOz: median(perOzValues),
    perOzCount: perOzValues.length,
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

// --- Opportunity Score -----------------------------------------------------
// The per-card composite defined in CONTEXT.md: "is this listing worth my
// attention *within this result set*?" 40% demand, 38% quality, 22% value,
// each normalized against the same search's results. Cohort-relative on
// purpose — 800 reviews means one thing among phone cases and another among
// espresso machines, and the only cohort we can honestly compare within is the
// set the user is looking at.
//
// Three decisions worth stating, because none is forced by the formula:
//
//   Demand is log-scaled before normalizing. Review counts span four orders of
//   magnitude in a single result set (4,809 to 203,137 on the live Kitchen
//   chart); linear normalization would hand ~1.0 to the single biggest listing
//   and ~0.0 to everything else, making the component a one-hot flag for
//   "is this the most-reviewed row" rather than a measure of demand.
//
//   Quality is the raw star rating, matching the documented formula rather
//   than the Wilson bound in ratingConfidenceScore() above. Deliberate: demand
//   already carries review count at 40%, so scoring quality on a
//   review-count-adjusted rating would spend most of the composite on the same
//   underlying number. The confidence-adjusted figure is still shown on the
//   card as its own line, where the user can weigh it directly.
//
//   Value rewards being priced BELOW the set's median. This is the
//   conventional reading of "value" and it is genuinely arguable — a category
//   manager hunting margin may want the opposite — so the card shows the raw
//   percent difference from the median next to it rather than only the
//   normalized component.
const OPPORTUNITY_WEIGHTS = { demand: 0.4, quality: 0.38, value: 0.22 };

function minMax(values) {
  const finite = values.filter((v) => v != null && Number.isFinite(v));
  if (!finite.length) return null;
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  return { lo, hi, span: hi - lo };
}

// Position of `v` in [lo, hi] as 0-1. A set where every listing shares a value
// carries no information to rank on, so everything lands mid-scale rather than
// at an arbitrary end.
function normalize(v, bounds) {
  if (v == null || !Number.isFinite(v) || !bounds) return null;
  if (bounds.span === 0) return 0.5;
  return Math.min(1, Math.max(0, (v - bounds.lo) / bounds.span));
}

/**
 * Opportunity Scores for a result set, aligned by index with `products`.
 *
 * Each entry is null for an unscoreable listing — per CONTEXT.md, one with no
 * rating *and* no reviews (a Pinterest pin, say) — so the card can omit the
 * chip entirely instead of printing a midpoint nobody measured.
 *
 * A listing missing only *some* inputs (priced but unrated, say) is still
 * scored, with the weights renormalized across the components it does have.
 * The alternative, scoring the absent component zero, would rank a listing
 * whose price the store simply didn't publish below one that is genuinely bad.
 */
export function opportunityScores(products) {
  const list = products || [];
  if (!list.length) return [];

  const prices = list.map((p) => parsePrice(p.price_text));
  const medianPrice = median(prices.filter((v) => v != null));

  // Value is distance below the median, so a listing at half the median scores
  // above one at the median, and the bounds come from the same set.
  const valueRaw = prices.map((v) => (v == null || medianPrice == null ? null : medianPrice - v));

  const demandRaw = list.map((p) =>
    p.review_count != null && p.review_count >= 0 ? Math.log1p(p.review_count) : null
  );
  const qualityRaw = list.map((p) => (p.rating != null ? p.rating : null));

  const demandBounds = minMax(demandRaw);
  const qualityBounds = minMax(qualityRaw);
  const valueBounds = minMax(valueRaw);

  return list.map((p, i) => {
    const unscoreable = p.rating == null && p.review_count == null;
    if (unscoreable) return null;

    const demand = normalize(demandRaw[i], demandBounds);
    const quality = normalize(qualityRaw[i], qualityBounds);
    const value = normalize(valueRaw[i], valueBounds);

    const parts = [
      ["demand", demand],
      ["quality", quality],
      ["value", value],
    ].filter(([, v]) => v != null);
    if (!parts.length) return null;

    const totalWeight = parts.reduce((sum, [k]) => sum + OPPORTUNITY_WEIGHTS[k], 0);
    const weighted = parts.reduce((sum, [k, v]) => sum + OPPORTUNITY_WEIGHTS[k] * v, 0);

    const price = prices[i];
    return {
      score: Math.round((weighted / totalWeight) * 100),
      demand,
      quality,
      value,
      // Shown beside the value component so the direction of the judgement is
      // visible: negative is cheaper than the set's median.
      priceVsMedianPct:
        price != null && medianPrice ? ((price - medianPrice) / medianPrice) * 100 : null,
      ratingConfidence: ratingConfidenceScore(p),
      // Which components actually contributed, so the card can mark a score
      // built from partial evidence rather than presenting it as complete.
      basis: parts.map(([k]) => k),
    };
  });
}

// --- Unit-price normalization ($/oz) ---------------------------------------
// A 40oz tumbler at $34.99 and a 24oz at $24.99 cannot be compared on sticker
// price, which is exactly the comparison the Market Snapshot exists to support.
// The size is almost always in the title, because that is where these stores
// put it, so it is parsed from there rather than fetched.
//
// Conservative by design: anything ambiguous returns null and the listing shows
// no unit price at all. A wrong $/oz is worse than none — it silently reorders
// the grid and the user has no way to see that the parse misfired.

// Volume units normalized to fluid ounces. Weight (oz/lb/g) is deliberately
// NOT converted into the same scale: an ounce of coffee and a fluid ounce of
// water are different quantities, and pooling them would produce a column that
// compares mass to volume.
const VOLUME_TO_OZ = {
  oz: 1,
  "fl oz": 1,
  ounce: 1,
  ounces: 1,
  ml: 0.033814,
  l: 33.814,
  liter: 33.814,
  litre: 33.814,
  liters: 33.814,
  qt: 32,
  quart: 32,
  quarts: 32,
  gal: 128,
  gallon: 128,
  cup: 8,
  cups: 8,
};

const SIZE_RE = new RegExp(
  String.raw`(\d+(?:\.\d+)?)\s*-?\s*(fl\.?\s*oz|ounces?|oz|ml|liters?|litres?|l|quarts?|qt|gallons?|gal|cups?)\b`,
  "i"
);

// "2-Pack", "Set of 16", "4 Pack", "Pack of 3".
const PACK_RES = [
  /\bset\s+of\s+(\d{1,3})\b/i,
  /\bpack\s+of\s+(\d{1,3})\b/i,
  /\b(\d{1,3})\s*-?\s*(?:pack|pk|count|ct|pcs|pieces)\b/i,
];

/** Total fluid ounces a listing represents, or null when the title doesn't say. */
export function parseVolumeOz(title) {
  if (!title) return null;
  const m = SIZE_RE.exec(String(title));
  if (!m) return null;
  const value = parseFloat(m[1]);
  if (!Number.isFinite(value) || value <= 0) return null;

  let unit = m[2].toLowerCase().replace(/\./g, "").replace(/\s+/g, " ").trim();
  if (unit.startsWith("fl")) unit = "fl oz";
  const factor = VOLUME_TO_OZ[unit];
  if (!factor) return null;

  const oz = value * factor;
  // A single vessel over five gallons is a parse artefact, not a tumbler.
  if (oz <= 0 || oz > 640) return null;
  return oz * (parsePackCount(String(title)) || 1);
}

/** Units in the pack, or null when the title doesn't say (treated as 1). */
export function parsePackCount(title) {
  if (!title) return null;
  for (const re of PACK_RES) {
    const m = re.exec(String(title));
    if (m) {
      const n = parseInt(m[1], 10);
      if (Number.isFinite(n) && n > 0 && n <= 500) return n;
    }
  }
  return null;
}

/** Price per fluid ounce, or null when either half is unknown. */
export function pricePerOz(product) {
  if (!product) return null;
  const price = parsePrice(product.price_text);
  if (price == null || price <= 0) return null;
  const oz = parseVolumeOz(product.title);
  if (oz == null) return null;
  return price / oz;
}
