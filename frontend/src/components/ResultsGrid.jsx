import { useState } from "react";
import ProductCard from "./ProductCard";
import { useI18n } from "../i18n";
import { exportProductsToExcel } from "../exportExcel";

export default function ResultsGrid({ products, hasMore = false, onLoadMore, loadingMore = false }) {
  const { t } = useI18n();
  const [exporting, setExporting] = useState(false);

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
        <button type="button" className="secondary-button" onClick={handleExport} disabled={exporting}>
          {exporting ? t("exporting") : t("exportExcel")}
        </button>
      </div>
      <div className="results-grid">
        {products.map((product, i) => (
          <ProductCard key={`${product.product_url}-${i}`} product={product} />
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
