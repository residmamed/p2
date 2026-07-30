import { useI18n } from "../i18n";
import { formatUSD } from "../productMetrics";
import "./ProductMetrics.css";

/* The per-card metrics panel: the Opportunity Score and the three components
   behind it, plus the two facts a composite always hides — how much evidence
   is under the rating, and what actually produced this listing's position.

   Everything here is relative to the current result set, which is stated on the
   panel rather than left for the user to assume. A score of 78 does not mean
   "good product"; it means "78th percentile of what this search returned".
*/

const BASIS_KEY = {
  bestseller_sort: "rankBasisBestseller",
  sold_count: "rankBasisSold",
  rating: "rankBasisRating",
  relevance: "rankBasisRelevance",
};

function Bar({ label, value, hint }) {
  const pct = value == null ? 0 : Math.round(value * 100);
  return (
    <div className="pm-bar-row" title={hint}>
      <span className="pm-bar-label">{label}</span>
      <span className="pm-bar-track" aria-hidden="true">
        <i style={{ width: `${value == null ? 0 : Math.max(2, pct)}%` }} />
      </span>
      <span className="pm-bar-value">{value == null ? "—" : pct}</span>
    </div>
  );
}

export default function ProductMetrics({ product, metrics }) {
  const { t } = useI18n();

  // Per CONTEXT.md an unscoreable listing shows no chip rather than a fake
  // midpoint — but saying *why* costs one line and stops it reading as a bug.
  if (!metrics) {
    return (
      <div className="product-metrics product-metrics-empty">{t("metricUnscoreable")}</div>
    );
  }

  const partial = metrics.basis.length < 3;
  const vsMedian = metrics.priceVsMedianPct;

  return (
    <div className="product-metrics">
      <div className="pm-head">
        <span className="pm-score-chip" title={t("opportunityHint")}>
          <b>{metrics.score}</b>
          <span className="pm-score-label">{t("opportunityScore")}</span>
        </span>
        {partial && <span className="pm-partial">{t("metricPartial")}</span>}
      </div>

      <Bar label={t("metricDemand")} value={metrics.demand} hint={t("metricDemandHint")} />
      <Bar label={t("metricQuality")} value={metrics.quality} hint={t("metricQualityHint")} />
      <Bar label={t("metricValue")} value={metrics.value} hint={t("metricValueHint")} />

      <div className="pm-facts">
        {metrics.ratingConfidence != null && product.rating != null && (
          <div className="pm-fact" title={t("metricRatingAdjHint")}>
            <span>{t("metricRatingAdj")}</span>
            <span className="pm-fact-value">
              {metrics.ratingConfidence.toFixed(2)}
              <em> / {product.rating.toFixed(1)}</em>
            </span>
          </div>
        )}
        {vsMedian != null && (
          <div className="pm-fact" title={t("metricVsMedianHint")}>
            <span>{t("metricVsMedian")}</span>
            <span
              className={`pm-fact-value ${vsMedian < 0 ? "pm-below" : vsMedian > 0 ? "pm-above" : ""}`}
            >
              {vsMedian > 0 ? "+" : ""}
              {vsMedian.toFixed(0)}%
            </span>
          </div>
        )}
        {product.review_count != null && (
          <div className="pm-fact">
            <span>{t("metricReviews")}</span>
            <span className="pm-fact-value">{product.review_count.toLocaleString()}</span>
          </div>
        )}
        {product.price_min != null && product.price_max != null &&
          product.price_max > product.price_min && (
            <div className="pm-fact">
              <span>{t("metricPriceRange")}</span>
              <span className="pm-fact-value">
                {formatUSD(product.price_min)}–{formatUSD(product.price_max)}
              </span>
            </div>
          )}
        {/* The honesty line: which signal actually produced this row's position.
            A relevance-ordered listing must never read as a best seller. */}
        {product.rank_basis && (
          <div className="pm-fact pm-basis">
            <span>{t("rankBasisLabel")}</span>
            <span className="pm-fact-value">
              {t(BASIS_KEY[product.rank_basis] || "rankBasisRelevance")}
              {product.site_rank != null && <em> · #{product.site_rank}</em>}
            </span>
          </div>
        )}
      </div>

      <div className="pm-footnote">{t("metricCohortNote")}</div>
    </div>
  );
}
