import { useMemo, useState } from "react";
import ProductCard from "./ProductCard";
import { useI18n } from "../i18n";
import { exportProductsToExcel } from "../exportExcel";
import { opportunityScores } from "../productMetrics";
import { useStoredState } from "../store";

export default function ResultsGrid({ products, hasMore = false, onLoadMore, loadingMore = false }) {
  const { t } = useI18n();
  const [exporting, setExporting] = useState(false);
  // Persisted: a buyer who works with the metrics open wants them open on the
  // next search too, and re-toggling on every query is pure friction.
  const [showMetrics, setShowMetrics] = useStoredState("p2_show_metrics", false);

  // Cohort-relative by definition, so it is computed once over the whole set
  // rather than per card. Recomputed only when the set itself changes.
  const metrics = useMemo(() => opportunityScores(products), [products]);

  if (products.length === 0) {
    return null;
  }

  async function handleExport() {
    setExporting(true);
    try {
      await exportProductsToExcel(products);
    } finally {
      setExporting(false);
    }
  }

  return (
    <>
      <div className="export-row">
        <button
          type="button"
          className={`secondary-button metrics-toggle ${showMetrics ? "metrics-toggle-on" : ""}`}
          onClick={() => setShowMetrics(!showMetrics)}
          aria-pressed={showMetrics}
          title={t("metricsToggleHint")}
        >
          {showMetrics ? t("metricsHide") : t("metricsShow")}
        </button>
        <button type="button" className="secondary-button" onClick={handleExport} disabled={exporting}>
          {exporting ? t("exporting") : t("exportExcel")}
        </button>
      </div>
      <div className="results-grid">
        {products.map((product, i) => (
          <ProductCard
            key={`${product.product_url}-${i}`}
            product={product}
            metrics={metrics[i]}
            showMetrics={showMetrics}
          />
        ))}
      </div>
      {hasMore && (
        <div className="load-more-row">
          <button type="button" className="secondary-button" onClick={onLoadMore} disabled={loadingMore}>
            {t("loadMore")}
          </button>
        </div>
      )}
    </>
  );
}
