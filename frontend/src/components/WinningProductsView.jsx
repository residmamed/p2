import { useEffect, useMemo, useState } from "react";
import { fetchWinningProducts } from "../api";
import "./WinningProducts.css";

/* Winning products — ranked discovery over Amazon's own category charts.

   Every number here comes from a live chart scan (backend app/winning.py); the
   only thing the UI decides is how to draw it. Two rules the mock didn't have
   to worry about and this does:

     1. The sparkline draws observed chart positions and nothing else. A product
        we've seen once has no line — not a flat one, which would read as "this
        product isn't moving" when the truth is "we haven't watched it yet".
     2. Momentum always shows its basis. An inference from a single scan and a
        measured rank delta are different claims and must not look alike.
*/

const BASIS_LABEL = {
  observed: "measured",
  rank_vs_depth: "inferred",
  none: "no data",
};

const BASIS_HELP = {
  observed: "Rank movement measured between recorded scans of this chart.",
  rank_vs_depth:
    "Inferred from a single scan: this product's chart rank against the review mass behind it. A high rank on a thin review base suggests a new or accelerating product — but a category where buyers rarely review looks the same, so this is a candidate signal, not a growth rate.",
  none: "No time evidence for this product yet.",
};

/* Rank is inverted (1 is best), so the path is flipped against the raw value. */
function Spark({ points, delay = 0, w = 82, h = 24 }) {
  if (!points || points.length < 2) return null;
  const max = Math.max(...points);
  const min = Math.min(...points);
  const span = max - min || 1;
  const d = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = ((p - min) / span) * (h - 4) + 2; // low rank = high on screen
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  const rising = points[points.length - 1] < points[0];
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" className="spark">
      <path
        className="spark-line"
        d={d}
        fill="none"
        stroke={rising ? "var(--rise)" : "var(--fall)"}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ animationDelay: `${delay}ms` }}
      />
    </svg>
  );
}

/* An un-drawable series is stated, never filled in. */
function NoSpark({ snapshots }) {
  return (
    <span className="nospark" title={`${snapshots} snapshot(s) recorded — a line needs at least 2.`}>
      {snapshots < 1 ? "not scanned" : `${snapshots} scan${snapshots === 1 ? "" : "s"}`}
    </span>
  );
}

function Momentum({ p }) {
  if (p.momentum_basis === "observed" && p.momentum_positions != null) {
    const moved = p.momentum_positions;
    if (moved === 0) {
      return (
        <span className="delta muted" title="Held its exact chart position between scans.">
          — held
        </span>
      );
    }
    const up = moved > 0;
    // Places, not percent: "up 30 places" is the sentence a buyer reasons with.
    // The share of chart depth rides in the tooltip for anyone who wants it.
    return (
      <span
        className="delta"
        style={{ color: up ? "var(--rise)" : "var(--fall)" }}
        title={`${Math.abs(p.momentum_pct)}% of the chart's depth, across ${p.snapshots} scans`}
      >
        {up ? "▲" : "▼"} {Math.abs(moved)}
        <em className="delta-unit">{Math.abs(moved) === 1 ? "place" : "places"}</em>
      </span>
    );
  }
  if (p.breakout != null) {
    return (
      <span className="delta inferred" title={BASIS_HELP.rank_vs_depth}>
        {(p.breakout * 100).toFixed(0)}
        <em>inf.</em>
      </span>
    );
  }
  return <span className="delta muted">—</span>;
}

function Thumb({ p, size }) {
  if (p.image) {
    return <img className="thumb" src={p.image} alt="" width={size} height={size} loading="lazy" />;
  }
  return <div className="thumb thumb-empty" style={{ width: size, height: size }} />;
}

function compactNum(n) {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export default function WinningProductsView() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [newOnly, setNewOnly] = useState(false);
  const [open, setOpen] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchWinningProducts({ category: "kitchen" })
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(String(e.message || e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const rows = useMemo(() => {
    const all = data?.products || [];
    return newOnly ? all.filter((p) => p.is_new_release) : all;
  }, [data, newOnly]);

  const lead = rows.slice(0, 3);
  const rest = rows.slice(3);

  if (loading) return <div className="wp"><div className="wp-state">Loading chart scan…</div></div>;
  if (error) return <div className="wp"><div className="wp-state wp-error">{error}</div></div>;

  const scannedAt = data?.scanned_at ? new Date(data.scanned_at).toLocaleString() : "never";

  return (
    <div className="wp">
      <header className="band">
        <div className="band-top">
          <div>
            <div className="eyebrow">
              {data.category_label} · {data.source === "live" ? "live scan" : "captured scan"} ·{" "}
              {scannedAt}
            </div>
            <h1 className="h1">Winning products</h1>
          </div>
          <div className="latency mono">
            {rows.length} of {data.products.length} · <b>{data.latency_ms} ms</b>
          </div>
        </div>
        <div className="controls">
          <button className="chip" aria-pressed={!newOnly} onClick={() => setNewOnly(false)}>
            All products
          </button>
          <button className="chip" aria-pressed={newOnly} onClick={() => setNewOnly(true)}>
            New releases only
          </button>
          <span className="snapcount mono">
            {data.snapshots} snapshot{data.snapshots === 1 ? "" : "s"}
          </span>
        </div>
      </header>

      <main className="sheet">
        {data.warnings?.length > 0 && (
          <div className="notice">
            {data.warnings.map((w, i) => (
              <div key={i}>{w}</div>
            ))}
          </div>
        )}

        <div className="lead">
          {lead.map((p, i) => (
            <button key={p.asin} className="lead-card" onClick={() => setOpen(p)}>
              <div className="lead-head">
                <Thumb p={p} size={62} />
                <div style={{ textAlign: "right" }}>
                  <div className="rank-lg mono">CHART #{String(p.rank).padStart(2, "0")}</div>
                  <div className="score-lg mono">{p.score.toFixed(1)}</div>
                </div>
              </div>
              <div>
                <div className="lead-title">{p.title}</div>
                <div className="lead-meta">
                  ★ {p.rating ?? "—"} · {compactNum(p.ratings_total)} reviews
                  {p.is_new_release && <span className="tag-new">NEW</span>}
                </div>
              </div>
              <div className="lead-foot">
                {p.rank_history?.length >= 2 ? (
                  <Spark points={p.rank_history} delay={i * 90} />
                ) : (
                  <NoSpark snapshots={p.snapshots} />
                )}
                <Momentum p={p} />
              </div>
            </button>
          ))}
        </div>

        <div className="list">
          <div className="list-head">
            <span>Score</span>
            <span />
            <span>Product</span>
            <span>Trend</span>
            <span>Momentum</span>
            <span>Reviews</span>
            <span style={{ textAlign: "right" }}>Chart</span>
          </div>
          {rest.map((p, i) => (
            <button key={p.asin} className="row" onClick={() => setOpen(p)}>
              <div className="r-score mono">{p.score.toFixed(1)}</div>
              <div className="hide-sm">
                <Thumb p={p} size={38} />
              </div>
              <div style={{ minWidth: 0 }}>
                <div className="r-title">{p.title}</div>
                <div className="r-sub">
                  ★ {p.rating ?? "—"}
                  {p.is_new_release && <span className="tag-new">NEW</span>}
                </div>
              </div>
              <div className="hide-sm">
                {p.rank_history?.length >= 2 ? (
                  <Spark points={p.rank_history} delay={Math.min(i, 14) * 45 + 260} />
                ) : (
                  <NoSpark snapshots={p.snapshots} />
                )}
              </div>
              <div>
                <Momentum p={p} />
              </div>
              <div className="num mono hide-sm">{compactNum(p.ratings_total)}</div>
              <div className="r-rank mono">#{p.rank}</div>
            </button>
          ))}
        </div>
      </main>

      {open && (
        <div className="drawer" onClick={() => setOpen(null)}>
          <aside className="panel" onClick={(e) => e.stopPropagation()}>
            <div className="eyebrow" style={{ color: "var(--muted)" }}>
              {open.asin} · chart #{open.rank} · {open.category_label}
            </div>
            <h3>{open.title}</h3>
            <div className="lead-meta" style={{ marginBottom: 20 }}>
              ★ {open.rating ?? "—"} · {compactNum(open.ratings_total)} reviews
              {open.link && (
                <>
                  {" · "}
                  <a href={open.link} target="_blank" rel="noreferrer">
                    open on Amazon
                  </a>
                </>
              )}
            </div>

            <div className="factor" style={{ borderTop: "1px solid var(--rule)" }}>
              <span>Composite score</span>
              <span style={{ fontSize: 20, fontWeight: 600 }}>{open.score.toFixed(1)}</span>
            </div>

            {[
              ["Momentum × 0.45", open.momentum_component],
              ["Chart standing × 0.35", open.demand_component],
              ["Rating × 0.20", open.quality_component],
            ].map(([label, v]) => (
              <div key={label} className="factor-row">
                <div className="factor-line">
                  <span>{label}</span>
                  <span className="mono">{(v ?? 0).toFixed(3)}</span>
                </div>
                <div className="bar">
                  <i style={{ width: `${Math.max(2, (v ?? 0) * 100)}%` }} />
                </div>
              </div>
            ))}

            <div className="basis-box">
              <div className="basis-head">
                Momentum basis: <b>{BASIS_LABEL[open.momentum_basis]}</b>
              </div>
              <p>{BASIS_HELP[open.momentum_basis]}</p>
              <p className="mono basis-stat">
                {open.snapshots} snapshot(s) recorded
                {open.momentum_basis === "observed" && open.momentum_positions != null
                  ? ` · moved ${open.momentum_positions > 0 ? "up" : "down"} ${Math.abs(
                      open.momentum_positions
                    )} place(s), ${Math.abs(open.momentum_pct)}% of chart depth`
                  : ""}
              </p>
            </div>

            <p className="footnote">
              Scored against the {open.category_label} chart cohort — percentiles are relative to
              the other {data.products.length} products in this scan, not to Amazon overall. Price
              is not shown: category charts don't publish it, and fetching it costs one API credit
              per product against 0.02 for everything above.
            </p>
            <button className="close" onClick={() => setOpen(null)}>
              Close
            </button>
          </aside>
        </div>
      )}
    </div>
  );
}
