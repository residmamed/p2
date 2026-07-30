import { useId, useMemo, useState } from "react";
import { marketSnapshot, formatUSD } from "../productMetrics";
import { SITE_LABELS, SITE_COLORS } from "../sites";
import { useI18n } from "../i18n";
import "./MarketSnapshot.css";

// 712400 -> "712.4k", 1300000 -> "1.3M"
function formatCompact(n) {
  if (n == null || !Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

function KpiTile({ label, children }) {
  return (
    <div className="ms-tile">
      <div className="ms-tile-label">{label}</div>
      <div className="ms-tile-value">{children}</div>
    </div>
  );
}

export default function MarketSnapshot({ products }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  const bodyId = useId();
  const snapshot = useMemo(() => marketSnapshot(products), [products]);

  if (!products || products.length === 0) return null;

  const compLabel = {
    low: t("msCompLow"),
    medium: t("msCompMedium"),
    high: t("msCompHigh"),
  }[snapshot.competition];

  const maxBucket = Math.max(...snapshot.histogram.map((b) => b.count), 1);

  return (
    <section className="market-snapshot">
      <div className="ms-header">
        <h3 className="ms-title">{t("msTitle")}</h3>
        <button
          type="button"
          className="ms-toggle"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={bodyId}
          aria-label={t("msToggle")}
        >
          <svg
            className={`ms-chevron ${open ? "" : "ms-chevron-closed"}`}
            width="16"
            height="16"
            viewBox="0 0 16 16"
            aria-hidden="true"
          >
            <path
              d="M4 6l4 4 4-4"
              stroke="currentColor"
              strokeWidth="1.5"
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>

      {open && (
        <div className="ms-body" id={bodyId}>
          <div className="ms-tiles">
            <KpiTile label={t("msResults")}>{snapshot.count}</KpiTile>
            <KpiTile label={t("msMedianPrice")}>{formatUSD(snapshot.medianPrice)}</KpiTile>
            <KpiTile label={t("msPriceRange")}>
              {snapshot.pricedCount > 0
                ? `${formatUSD(snapshot.minPrice)} – ${formatUSD(snapshot.maxPrice)}`
                : "—"}
            </KpiTile>
            <KpiTile label={t("msAvgRating")}>
              {snapshot.avgRating != null ? `★ ${snapshot.avgRating.toFixed(1)}` : "—"}
            </KpiTile>
            <KpiTile label={t("msTotalReviews")}>{formatCompact(snapshot.totalReviews)}</KpiTile>
            {/* Only rendered when some listing actually stated a size. The
                count it is based on rides along, because a median over 3 of 42
                listings is a different claim from one over all 42. */}
            {snapshot.perOzCount > 0 && (
              <KpiTile label={t("msPerOz")}>
                ${snapshot.medianPerOz.toFixed(2)}
                <span className="ms-tile-sub">
                  {t("msPerOzOf").replace("{n}", String(snapshot.perOzCount))}
                </span>
              </KpiTile>
            )}
            <KpiTile label={t("msCompetition")}>
              <span
                className={`ms-chip ms-chip-${snapshot.competition}`}
                title={t("msCompHint")}
              >
                {compLabel}
              </span>
            </KpiTile>
          </div>

          <div className="ms-detail">
            {snapshot.pricedCount > 0 && (
              <div className="ms-block">
                <div className="ms-block-label">{t("msPriceDistribution")}</div>
                <div className="ms-hist-bars">
                  {snapshot.histogram.map((b, i) => (
                    <div
                      key={i}
                      className={`ms-hist-bar ${b.count === 0 ? "ms-hist-bar-empty" : ""}`}
                      style={b.count > 0 ? { height: `${(b.count / maxBucket) * 100}%` } : undefined}
                      title={`${formatUSD(b.lo)}–${formatUSD(b.hi)} · ${t("msHistProducts").replace("{0}", String(b.count))}`}
                    />
                  ))}
                </div>
                <div className="ms-hist-axis">
                  <span>{formatUSD(snapshot.minPrice)}</span>
                  <span>{formatUSD(snapshot.maxPrice)}</span>
                </div>
              </div>
            )}

            <div className="ms-block">
              <div className="ms-block-label">{t("msSiteMix")}</div>
              <div className="ms-sitemix-bar">
                {snapshot.siteMix.map((s) => (
                  <div
                    key={s.site}
                    className="ms-sitemix-segment"
                    style={{
                      width: `${s.pct * 100}%`,
                      background: SITE_COLORS[s.site] || "#888",
                    }}
                    title={`${SITE_LABELS[s.site] || s.site} · ${s.count}`}
                  />
                ))}
              </div>
              <div className="ms-sitemix-legend">
                {snapshot.siteMix.map((s) => (
                  <span key={s.site} className="ms-legend-item">
                    <span
                      className="ms-legend-dot"
                      style={{ background: SITE_COLORS[s.site] || "#888" }}
                    />
                    {SITE_LABELS[s.site] || s.site}
                    <span className="ms-legend-count">{s.count}</span>
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
