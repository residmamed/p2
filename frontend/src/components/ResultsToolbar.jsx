import { useI18n } from "../i18n";
import { opportunityScores, parsePrice, pricePerOz, ratingConfidenceScore } from "../productMetrics";
import "./ResultsToolbar.css";

export const DEFAULT_FILTERS = {
  q: "",
  sort: "default",
  priceMin: "",
  priceMax: "",
  minRating: 0,
  minReviews: "",
};

const RATING_STOPS = [0, 3, 4, 4.5];

// Which sorts are descending; everything else ascending.
const DESC_SORTS = new Set(["priceDesc", "rating", "reviews", "opportunity"]);

// Pure filter + sort over a product result set. Never mutates the input.
export function applyResultFilters(products, filters) {
  const list = products || [];
  const f = { ...DEFAULT_FILTERS, ...filters };

  const q = String(f.q).trim().toLowerCase();
  const priceMin = parseFloat(f.priceMin);
  const priceMax = parseFloat(f.priceMax);
  const hasPriceMin = Number.isFinite(priceMin);
  const hasPriceMax = Number.isFinite(priceMax);
  const minRating = Number(f.minRating) || 0;
  const minReviews = parseFloat(f.minReviews);
  const hasMinReviews = Number.isFinite(minReviews);

  const out = list.filter((p) => {
    if (q && !String(p.title || "").toLowerCase().includes(q)) return false;
    if (hasPriceMin || hasPriceMax) {
      // Unpriced listings only drop out once the user actually bounds price.
      const price = parsePrice(p.price_text);
      if (price == null) return false;
      if (hasPriceMin && price < priceMin) return false;
      if (hasPriceMax && price > priceMax) return false;
    }
    if (minRating > 0 && !(p.rating != null && p.rating >= minRating)) return false;
    if (hasMinReviews && !(p.review_count != null && p.review_count >= minReviews)) return false;
    return true;
  });

  if (f.sort === "default") return out;

  // Opportunity is cohort-relative, so it is scored over the WHOLE input set
  // rather than the filtered survivors — otherwise narrowing the grid would
  // silently restate every score, and a card would disagree with the sort that
  // placed it. Keyed by object identity, which holds: `out` is a filtered view
  // of these same objects.
  let opportunityByProduct = null;
  if (f.sort === "opportunity") {
    opportunityByProduct = new Map();
    const scored = opportunityScores(list);
    list.forEach((p, i) => opportunityByProduct.set(p, scored[i]?.score ?? null));
  }

  const keyFor = {
    priceAsc: (p) => parsePrice(p.price_text),
    priceDesc: (p) => parsePrice(p.price_text),
    // Not the raw star value — the pessimistic end of a confidence interval
    // around it, so a rating only ranks as high as its review count can
    // justify. See ratingConfidenceScore.
    rating: ratingConfidenceScore,
    reviews: (p) => p.review_count ?? null,
    // Cheapest per fluid ounce first. Listings whose title never stated a size
    // key as null and sort to the end rather than being dropped.
    perOz: pricePerOz,
    opportunity: (p) => opportunityByProduct.get(p) ?? null,
  }[f.sort];
  if (!keyFor) return out;

  const keys = new Map(out.map((p) => [p, keyFor(p)]));
  const desc = DESC_SORTS.has(f.sort);
  return out.sort((a, b) => {
    const ka = keys.get(a);
    const kb = keys.get(b);
    if (ka == null && kb == null) return 0;
    if (ka == null) return 1;
    if (kb == null) return -1;
    if (ka !== kb) return desc ? kb - ka : ka - kb;
    // Equal weighted scores: the better-evidenced listing goes first, so the
    // order stays explainable instead of falling back to arrival order.
    if (f.sort === "rating") return (b.review_count ?? 0) - (a.review_count ?? 0);
    return 0;
  });
}

export default function ResultsToolbar({ filters, onChange, shownCount, totalCount }) {
  const { t } = useI18n();
  const f = { ...DEFAULT_FILTERS, ...filters };
  const set = (field, value) => onChange({ ...filters, [field]: value });
  const isDirty = Object.keys(DEFAULT_FILTERS).some((k) => f[k] !== DEFAULT_FILTERS[k]);

  return (
    <div className="results-toolbar">
      <input
        type="text"
        className="rt-input rt-search"
        placeholder={t("rtRefine")}
        aria-label={t("rtRefine")}
        value={f.q}
        onChange={(e) => set("q", e.target.value)}
      />

      <select
        className="rt-input rt-select"
        aria-label={t("rtSortLabel")}
        value={f.sort}
        onChange={(e) => set("sort", e.target.value)}
      >
        <option value="default">{t("rtSortDefault")}</option>
        <option value="priceAsc">{t("rtSortPriceAsc")}</option>
        <option value="priceDesc">{t("rtSortPriceDesc")}</option>
        <option value="rating">{t("rtSortRating")}</option>
        <option value="reviews">{t("rtSortReviews")}</option>
        <option value="opportunity">{t("rtSortOpportunity")}</option>
        <option value="perOz">{t("rtSortPerOz")}</option>
      </select>

      <span className="rt-price-wrap">
        <span className="rt-currency" aria-hidden="true">$</span>
        <input
          type="number"
          className="rt-input rt-price"
          min="0"
          step="0.5"
          placeholder={t("rtMin")}
          aria-label={t("rtPriceMin")}
          value={f.priceMin}
          onChange={(e) => set("priceMin", e.target.value)}
        />
      </span>
      <span className="rt-price-wrap">
        <span className="rt-currency" aria-hidden="true">$</span>
        <input
          type="number"
          className="rt-input rt-price"
          min="0"
          step="0.5"
          placeholder={t("rtMax")}
          aria-label={t("rtPriceMax")}
          value={f.priceMax}
          onChange={(e) => set("priceMax", e.target.value)}
        />
      </span>

      <div className="rt-rating-group" role="group" aria-label={t("rtRatingLabel")}>
        {RATING_STOPS.map((r) => (
          <button
            key={r}
            type="button"
            className={`rt-pill ${Number(f.minRating) === r ? "active" : ""}`}
            aria-pressed={Number(f.minRating) === r}
            onClick={() => set("minRating", r)}
          >
            {r === 0 ? t("rtRatingAny") : `★ ${r}+`}
          </button>
        ))}
      </div>

      <input
        type="number"
        className="rt-input rt-reviews"
        min="0"
        step="1"
        placeholder={t("rtMinReviews")}
        aria-label={t("rtMinReviews")}
        value={f.minReviews}
        onChange={(e) => set("minReviews", e.target.value)}
      />

      <span className="rt-count" aria-live="polite">
        {t("rtShown")} <strong>{shownCount}</strong> / {totalCount}
      </span>
      {isDirty && (
        <button type="button" className="rt-clear" onClick={() => onChange({ ...DEFAULT_FILTERS })}>
          {t("rtClear")}
        </button>
      )}
    </div>
  );
}
