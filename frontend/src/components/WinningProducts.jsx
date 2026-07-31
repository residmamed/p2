import React, { useCallback, useEffect, useMemo, useState } from "react";
import { searchBestSellers } from "../api";
import { buildBoard } from "../trendMetrics";
import { parsePrice, formatUSD } from "../productMetrics";
import { SITE_LABELS } from "../sites";

/* ------------------------------------------------------------------
   Winning products — a ranked board over real Product Search results.

   Nothing here is a fixture. On mount it picks a handful of category
   seeds at random, runs them through the same /api/bestsellers the
   Product Search tab uses, and ranks whatever comes back.

   What the rows show is deliberately two things at once: the listing's
   real demand signals (rating, review count, store rank) and a MODELED
   trajectory over them — see trendMetrics.js. The stores return no
   history, so momentum and the velocity curve are estimates, and every
   place they appear says so rather than passing them off as measured.
------------------------------------------------------------------ */

// The pool the board draws from. Each seed is one /api/bestsellers query, so
// these are keywords a store can actually answer, not taxonomy nodes.
const SEEDS = [
  { category: "Home & Kitchen", query: "pour over coffee kettle" },
  { category: "Home & Kitchen", query: "silicone bakeware set" },
  { category: "Home & Kitchen", query: "cast iron grill press" },
  { category: "Home & Kitchen", query: "glass storage containers" },
  { category: "Beauty", query: "vitamin c face serum" },
  { category: "Beauty", query: "heatless curling rod" },
  { category: "Beauty", query: "lip sleeping mask" },
  { category: "Pet Supplies", query: "slow feeder dog bowl" },
  { category: "Pet Supplies", query: "cat water fountain" },
  { category: "Baby", query: "silicone baby feeding set" },
  { category: "Baby", query: "portable bottle warmer" },
  { category: "Fitness", query: "adjustable dumbbell set" },
  { category: "Fitness", query: "resistance band set" },
  { category: "Outdoor", query: "insulated tumbler 40 oz" },
  { category: "Outdoor", query: "collapsible wagon cart" },
  { category: "Office", query: "standing desk converter" },
  { category: "Office", query: "monitor light bar" },
];

// Three stores rather than all twelve: the board fans out over several queries
// at once, and each extra store multiplies that fan-out.
const BOARD_SITES = ["amazon", "walmart", "target"];

// How many seeds one board is built from.
const SEEDS_PER_BOARD = 4;

// Boards already built this session, keyed by their seed keywords. Every seed
// is a metered store search, and leaving the tab unmounts this component — so
// without a cache, switching to Product Search and back would silently re-bill
// the whole board. "↻ New keywords" picks a different key, so it still searches.
const _boardCache = new Map();

function pickSeeds(n) {
  const pool = [...SEEDS];
  const out = [];
  while (out.length < n && pool.length) {
    out.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
  }
  return out;
}

/* Momentum sparkline — draws itself in on mount, staggered by rank.
   This is the one moment of motion on the page. It encodes data,
   so it reads as instrumentation rather than decoration. */
function Spark({ points, delay = 0, w = 76, h = 26, tone = "var(--rise)" }) {
  const max = Math.max(...points);
  const min = Math.min(...points);
  const span = max - min || 1;
  const d = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = h - ((p - min) / span) * (h - 4) - 2;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" style={{ display: "block", overflow: "visible" }}>
      <path
        className="spark-line"
        d={d}
        fill="none"
        stroke={tone}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ animationDelay: `${delay}ms` }}
      />
    </svg>
  );
}

function Delta({ value }) {
  const up = value >= 0;
  return (
    <span className="delta" style={{ color: up ? "var(--rise)" : "var(--fall)" }}>
      {up ? "▲" : "▼"} {Math.abs(value)}%
    </span>
  );
}

// The listing's own photo where it has one. Some stores return rows without an
// image_url, so the monogram is a fallback rather than the design.
function Thumb({ p, size }) {
  const [failed, setFailed] = useState(false);
  if (p.image_url && !failed) {
    return (
      <img
        className="thumb"
        src={p.image_url}
        alt=""
        loading="lazy"
        width={size}
        height={size}
        style={{ width: size, height: size, objectFit: "cover" }}
        onError={() => setFailed(true)}
      />
    );
  }
  const label = (p.seller_name || p.title || "?").trim();
  const hue = (label.length * 37 + (p.site?.length ?? 0) * 11) % 360;
  return (
    <div className="thumb" style={{ width: size, height: size, background: `hsl(${hue} 18% 92%)` }}>
      <span style={{ color: `hsl(${hue} 22% 42%)` }}>{label[0]?.toUpperCase() ?? "?"}</span>
    </div>
  );
}

function priceLabel(p) {
  const v = parsePrice(p.price_text);
  return v != null ? formatUSD(v) : p.price_text || "—";
}

export default function WinningProducts() {
  const [seeds, setSeeds] = useState(() => pickSeeds(SEEDS_PER_BOARD));
  const [all, setAll] = useState([]);
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [error, setError] = useState(null);
  const [warnings, setWarnings] = useState([]);
  const [latencyMs, setLatencyMs] = useState(null);
  const [category, setCategory] = useState("All categories");
  const [newOnly, setNewOnly] = useState(false);
  const [open, setOpen] = useState(null);

  const reshuffle = useCallback(() => {
    setCategory("All categories");
    setSeeds(pickSeeds(SEEDS_PER_BOARD));
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const cacheKey = seeds.map((s) => s.query).join("|");

    const cached = _boardCache.get(cacheKey);
    if (cached) {
      setAll(cached.rows);
      setWarnings(cached.warnings);
      setLatencyMs(cached.latencyMs);
      setStatus(cached.rows.length ? "ready" : "error");
      setError(cached.rows.length ? null : "No listings came back for these keywords.");
      return () => controller.abort();
    }

    setStatus("loading");
    setError(null);
    setWarnings([]);
    setAll([]);
    const started = performance.now();

    // Seeds run concurrently and stay independent: one keyword failing should
    // cost that keyword's rows, not the whole board.
    Promise.all(
      seeds.map(async (seed) => {
        try {
          const data = await searchBestSellers(seed.query, {
            sites: BOARD_SITES,
            signal: controller.signal,
          });
          return {
            results: (data.results ?? []).map((p) => ({
              ...p,
              category: seed.category,
              seed: seed.query,
            })),
            warnings: data.warnings ?? [],
          };
        } catch (e) {
          if (e.name === "AbortError") throw e;
          return { results: [], warnings: [`[${seed.query}] ${e.message}`] };
        }
      })
    )
      .then((batches) => {
        if (controller.signal.aborted) return;
        const rows = buildBoard(batches.flatMap((b) => b.results));
        const boardWarnings = batches.flatMap((b) => b.warnings);
        const took = Math.round(performance.now() - started);
        // Cached even when empty: a keyword set the stores had nothing for will
        // have nothing for it on the way back either.
        _boardCache.set(cacheKey, { rows, warnings: boardWarnings, latencyMs: took });
        setAll(rows);
        setWarnings(boardWarnings);
        setLatencyMs(took);
        setStatus(rows.length ? "ready" : "error");
        if (!rows.length) setError("No listings came back for these keywords.");
      })
      .catch((e) => {
        if (e.name === "AbortError" || controller.signal.aborted) return;
        setError(e.message);
        setStatus("error");
      });

    return () => controller.abort();
  }, [seeds]);

  const categories = useMemo(() => [...new Set(seeds.map((s) => s.category))], [seeds]);

  const rows = useMemo(
    () =>
      all.filter(
        (p) =>
          (category === "All categories" || p.category === category) &&
          (!newOnly || p.trend.ageDays < 365)
      ),
    [all, category, newOnly]
  );

  const lead = rows.slice(0, 3);
  const rest = rows.slice(3);

  return (
    <div className="wp">
      <style>{`
@import url('https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

.wp {
  --canvas:#EDF0F2; --surface:#fff; --ink:#14161A; --muted:#6E7A85;
  --plum:#3E1638; --plum-lift:#5D2955; --rise:#4F8F2C; --fall:#A85338;
  --rule:#D4DBE0;
  background:var(--canvas); color:var(--ink); min-height:100vh;
  font-family:'Instrument Sans',system-ui,sans-serif; -webkit-font-smoothing:antialiased;
}
.wp *{box-sizing:border-box;}
.mono{font-family:'IBM Plex Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums;}

/* header band — lead tiles overlap its lower edge for depth without shadow */
.band{background:var(--plum);padding:26px 32px 78px;}
.band-top{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:#C9A8C2;}
.h1{font-size:30px;font-weight:600;letter-spacing:-.02em;color:#fff;margin:4px 0 0;}
.band-right{display:flex;align-items:center;gap:8px;}
.latency{font-size:12px;color:#C9A8C2;border:1px solid var(--plum-lift);border-radius:2px;padding:5px 10px;}
.latency b{color:#fff;font-weight:500;}

.controls{display:flex;gap:8px;margin-top:18px;flex-wrap:wrap;}
.chip{background:transparent;border:1px solid var(--plum-lift);color:#E4CFE0;
  font:inherit;font-size:13px;padding:6px 13px;border-radius:2px;cursor:pointer;}
.chip[aria-pressed="true"]{background:#fff;border-color:#fff;color:var(--plum);}
.chip:disabled{opacity:.5;cursor:default;}
.chip:focus-visible{outline:2px solid #E9B44C;outline-offset:2px;}

.sheet{padding:0 32px 56px;margin-top:-56px;}
.note{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
  padding:14px 16px;font-size:13px;color:var(--muted);line-height:1.55;}
.note b{color:var(--ink);font-weight:500;}
.warn{margin-top:12px;font-size:12.5px;line-height:1.6;}

/* top three earn the visual splurge */
.lead{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}
.lead-card{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
  padding:16px;display:flex;flex-direction:column;gap:12px;cursor:pointer;text-align:left;
  font:inherit;color:inherit;transition:border-color .16s,transform .16s;}
.lead-card:hover{border-color:var(--plum);transform:translateY(-2px);}
.lead-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;}
.rank-lg{font-size:12px;letter-spacing:.1em;color:var(--muted);}
.score-lg{font-size:34px;font-weight:600;letter-spacing:-.03em;line-height:1;}
.lead-title{font-size:15px;font-weight:500;line-height:1.35;display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.lead-meta{font-size:12.5px;color:var(--muted);}
.lead-foot{display:flex;align-items:center;justify-content:space-between;
  border-top:1px solid var(--rule);padding-top:11px;margin-top:auto;}

.thumb{border-radius:2px;display:grid;place-items:center;flex:none;}
.thumb span{font-size:20px;font-weight:600;}

/* ranks 4–100: dense scan sheet, the vernacular of a buying report */
.list{margin-top:26px;background:var(--surface);border:1px solid var(--rule);border-radius:3px;}
.list-head,.row{display:grid;grid-template-columns:46px 44px 1fr 96px 92px 78px 58px;
  align-items:center;gap:14px;padding:0 16px;}
.list-head{height:34px;border-bottom:1px solid var(--rule);font-size:10.5px;
  letter-spacing:.12em;text-transform:uppercase;color:var(--muted);}
.row{height:62px;border-bottom:1px solid var(--rule);cursor:pointer;width:100%;
  font:inherit;color:inherit;background:none;border-left:0;border-right:0;border-top:0;text-align:left;}
.row:last-child{border-bottom:0;}
.row:hover{background:#F7F9FA;}
.row:focus-visible{outline:2px solid var(--plum);outline-offset:-2px;}
.r-num{font-size:12.5px;color:var(--muted);}
.r-title{font-size:14px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.r-sub{font-size:12px;color:var(--muted);margin-top:2px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
.r-score{font-size:17px;font-weight:600;text-align:right;}
.num{font-size:13px;}
.delta{font-size:12.5px;font-weight:500;font-family:'IBM Plex Mono',monospace;}
.est{font-size:9.5px;letter-spacing:.08em;color:var(--muted);}

.skeleton{background:var(--surface);border:1px solid var(--rule);border-radius:3px;height:158px;
  animation:pulse 1.4s ease-in-out infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.55}}

.spark-line{stroke-dasharray:200;stroke-dashoffset:200;animation:draw .85s cubic-bezier(.22,.8,.3,1) forwards;}
@keyframes draw{to{stroke-dashoffset:0;}}
@media (prefers-reduced-motion:reduce){
  .spark-line{animation:none;stroke-dashoffset:0;}
  .lead-card{transition:none;}
  .skeleton{animation:none;}
}

/* breakdown drawer — the score shows its work */
.drawer{position:fixed;inset:0;background:rgba(20,22,26,.42);display:flex;justify-content:flex-end;z-index:40;}
.panel{background:var(--surface);width:min(430px,100%);height:100%;padding:28px;overflow:auto;}
.panel h3{font-size:19px;font-weight:600;margin:6px 0 3px;letter-spacing:-.01em;}
.factor{display:flex;justify-content:space-between;align-items:center;gap:12px;
  padding:11px 0;border-bottom:1px solid var(--rule);font-size:13.5px;}
.factor span:last-child{font-family:'IBM Plex Mono',monospace;font-variant-numeric:tabular-nums;}
.bar{height:3px;background:var(--rule);border-radius:2px;overflow:hidden;margin-top:7px;}
.bar i{display:block;height:100%;background:var(--plum);}
.open-link{display:inline-block;margin-top:18px;font-size:13px;color:var(--plum);}
.close{background:none;border:1px solid var(--rule);border-radius:2px;font:inherit;font-size:13px;
  padding:7px 14px;cursor:pointer;margin-top:22px;}

@media (max-width:820px){
  .lead{grid-template-columns:1fr;}
  .list-head,.row{grid-template-columns:38px 1fr 84px 58px;}
  .list-head span:nth-child(2),.row .hide-sm{display:none;}
  .band,.sheet{padding-left:18px;padding-right:18px;}
}
      `}</style>

      <header className="band">
        <div className="band-top">
          <div>
            <div className="eyebrow">{seeds.map((s) => s.query).join(" · ")}</div>
            <h1 className="h1">Winning products</h1>
          </div>
          <div className="band-right">
            <div className="latency mono">
              {status === "loading" ? (
                "searching…"
              ) : (
                <>
                  {rows.length} of {all.length} ·{" "}
                  <b>{latencyMs != null ? `${(latencyMs / 1000).toFixed(1)} s` : "—"}</b>
                </>
              )}
            </div>
            <button className="chip" onClick={reshuffle} disabled={status === "loading"}>
              ↻ New keywords
            </button>
          </div>
        </div>
        <div className="controls">
          {["All categories", ...categories].map((c) => (
            <button key={c} className="chip" aria-pressed={category === c} onClick={() => setCategory(c)}>
              {c}
            </button>
          ))}
          <button className="chip" aria-pressed={newOnly} onClick={() => setNewOnly(!newOnly)}>
            New entrants only
          </button>
        </div>
      </header>

      <main className="sheet">
        {status === "loading" && (
          <div className="lead">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" />
            ))}
          </div>
        )}

        {status === "error" && (
          <div className="note">
            <b>Nothing to rank.</b> {error}
            {warnings.length > 0 && <div className="warn">{warnings.join(" ")}</div>}
          </div>
        )}

        {status === "ready" && (
          <>
            <div className="lead">
              {lead.map((p, i) => (
                <button key={p.product_url} className="lead-card" onClick={() => setOpen(p)}>
                  <div className="lead-head">
                    <Thumb p={p} size={62} />
                    <div style={{ textAlign: "right" }}>
                      <div className="rank-lg mono">RANK {String(p.rank).padStart(2, "0")}</div>
                      <div className="score-lg mono">{p.trend.score.toFixed(1)}</div>
                    </div>
                  </div>
                  <div>
                    <div className="lead-title">{p.title}</div>
                    <div className="lead-meta">
                      {SITE_LABELS[p.site] ?? p.site} · {priceLabel(p)}
                      {p.trend.reviews != null && ` · ${p.trend.reviews.toLocaleString()} reviews`}
                    </div>
                  </div>
                  <div className="lead-foot">
                    <Spark points={p.trend.velocity} delay={i * 90} />
                    <Delta value={p.trend.momentum} />
                  </div>
                </button>
              ))}
            </div>

            <div className="list">
              <div className="list-head">
                <span>Rank</span>
                <span></span>
                <span>Product</span>
                <span>
                  Velocity <span className="est">EST</span>
                </span>
                <span>
                  Momentum <span className="est">EST</span>
                </span>
                <span>Reviews</span>
                <span style={{ textAlign: "right" }}>Score</span>
              </div>
              {rest.map((p, i) => (
                <button key={p.product_url} className="row" onClick={() => setOpen(p)}>
                  <div className="r-num mono">{String(p.rank).padStart(3, "0")}</div>
                  <div className="hide-sm">
                    <Thumb p={p} size={38} />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div className="r-title">{p.title}</div>
                    <div className="r-sub">
                      {SITE_LABELS[p.site] ?? p.site} · {priceLabel(p)}
                    </div>
                  </div>
                  <div className="hide-sm">
                    <Spark points={p.trend.velocity} delay={Math.min(i, 14) * 45 + 260} w={82} h={22} />
                  </div>
                  <div>
                    <Delta value={p.trend.momentum} />
                  </div>
                  <div className="num mono hide-sm">
                    {p.trend.reviews != null ? p.trend.reviews.toLocaleString() : "—"}
                  </div>
                  <div className="r-score mono">{p.trend.score.toFixed(1)}</div>
                </button>
              ))}
            </div>

            <div className="note" style={{ marginTop: 20 }}>
              <b>How to read this.</b> Rating, review count and store rank are read off the live
              listings on {BOARD_SITES.map((s) => SITE_LABELS[s] ?? s).join(", ")}. The columns
              marked <span className="est">EST</span> — momentum and the velocity curve — are{" "}
              <b>modeled</b>: Product Search returns a listing's current state and no history, so
              there is no second observation to measure a trend against. They are estimates shaped
              by the real demand signals, stable per product, and not measurements.
              {warnings.length > 0 && <div className="warn">{warnings.join(" ")}</div>}
            </div>
          </>
        )}
      </main>

      {open && (
        <div className="drawer" onClick={() => setOpen(null)}>
          <aside className="panel" onClick={(e) => e.stopPropagation()}>
            <div className="eyebrow" style={{ color: "var(--muted)" }}>
              {SITE_LABELS[open.site] ?? open.site} · rank {open.rank} · “{open.seed}”
            </div>
            <h3>{open.title}</h3>
            <div className="lead-meta" style={{ marginBottom: 20 }}>
              {open.seller_name ? `${open.seller_name} · ` : ""}
              {open.category} · {priceLabel(open)}
            </div>

            <div className="factor" style={{ borderTop: "1px solid var(--rule)" }}>
              <span>Composite score</span>
              <span style={{ fontSize: 20, fontWeight: 600 }}>{open.trend.score.toFixed(1)}</span>
            </div>

            {[
              [
                "Demand signal — measured",
                `${Math.round(open.trend.demand * 100)} / 100`,
                Math.min(100, open.trend.demand * 100),
              ],
              [
                "Review volume — measured",
                open.trend.reviews != null ? open.trend.reviews.toLocaleString() : "not published",
                open.trend.reviews != null ? Math.min(100, Math.log10(open.trend.reviews + 10) * 25) : 6,
              ],
              [
                "Rating — measured",
                open.trend.rating != null
                  ? `${open.trend.rating.toFixed(2)}${
                      open.trend.confidence != null ? ` · ${open.trend.confidence.toFixed(2)} held` : ""
                    }`
                  : "not published",
                open.trend.rating != null ? (open.trend.rating / 5) * 100 : 6,
              ],
              [
                "Momentum, 90d — estimated",
                `${open.trend.momentum >= 0 ? "+" : ""}${open.trend.momentum}%`,
                Math.min(100, Math.max(4, open.trend.momentum / 2.4)),
              ],
              [
                "Reviews in trailing 90d — estimated",
                open.trend.recentReviews != null ? open.trend.recentReviews.toLocaleString() : "—",
                open.trend.recentReviews != null ? Math.min(100, Math.log10(open.trend.recentReviews + 10) * 28) : 6,
              ],
              [
                "Days since first review — estimated",
                `${open.trend.ageDays}`,
                Math.max(6, 100 - open.trend.ageDays / 15),
              ],
            ].map(([label, val, pct]) => (
              <div key={label} style={{ padding: "11px 0", borderBottom: "1px solid var(--rule)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13.5, gap: 12 }}>
                  <span>{label}</span>
                  <span className="mono">{val}</span>
                </div>
                <div className="bar">
                  <i style={{ width: `${pct}%` }} />
                </div>
              </div>
            ))}

            <p style={{ fontSize: 12.5, color: "var(--muted)", lineHeight: 1.6, marginTop: 18 }}>
              Scored against the other listings this board pulled for “{open.seed}”. The measured
              rows come from the live listing; the estimated ones are modeled from them, because the
              store publishes no review history to trend against.
            </p>

            <a className="open-link" href={open.product_url} target="_blank" rel="noreferrer">
              Open on {SITE_LABELS[open.site] ?? open.site} ↗
            </a>
            <div>
              <button className="close" onClick={() => setOpen(null)}>
                Close
              </button>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
