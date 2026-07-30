import { useEffect, useRef, useState } from "react";
import { SITE_LABELS, SITE_COLORS } from "../sites";
import { useI18n } from "../i18n";

// Something to watch while a search that takes minutes runs: the stores being
// searched scroll past, one line at a time, newest on top.
//
// Only the stores the user actually picked are named. Padding the list with
// marketplaces this run never touches would be more to look at and a lie about
// where the results came from — the whole point of the line is that it says
// what is happening.
const STEP_MS = 1500;
const KEEP = 3;

// Cycled per lap through the store list, so a run over two stores doesn't
// repeat the same two lines; the store changes every step, the verb every lap.
// A photo search gets its own wording: it never queries the stores by keyword,
// so "ranking best sellers" would describe work that isn't happening.
const VERBS = {
  product: ["tickerScanning", "tickerReading", "tickerRanking", "tickerCollecting"],
  lens: ["tickerLensLooking", "tickerLensExact", "tickerReading", "tickerCollecting"],
  supplier: ["tickerMatching", "tickerOpening", "tickerPricing", "tickerProfiling"],
};

export default function SearchTicker({ sites = [], variant = "product" }) {
  const { t } = useI18n();
  const [lines, setLines] = useState([]);
  const stepRef = useRef(0);
  // The effect keys off this rather than the array itself: `sites` is a fresh
  // array on every parent render, and restarting the ticker each time would
  // leave it stuck on its first line.
  const siteKey = sites.join(",");

  useEffect(() => {
    const list = siteKey ? siteKey.split(",") : [];
    if (!list.length) return;
    const verbs = VERBS[variant] || VERBS.product;
    stepRef.current = 0;
    function push() {
      const n = stepRef.current++;
      const line = {
        id: n,
        site: list[n % list.length],
        verb: verbs[Math.floor(n / list.length) % verbs.length],
      };
      setLines((prev) => [line, ...prev].slice(0, KEEP));
    }
    setLines([]);
    push(); // don't leave the row empty for the first step
    const timer = setInterval(push, STEP_MS);
    return () => clearInterval(timer);
  }, [siteKey, variant]);

  if (!sites.length) return null;

  return (
    // Decorative: the progress bar's own label is the status a screen reader
    // should read, and announcing a new line every 1.5s would talk over it.
    <div className="ticker" aria-hidden="true">
      {lines.map((line, depth) => (
        <div key={line.id} className="ticker-line" style={{ "--depth": depth }}>
          <span
            className="ticker-dot"
            style={{ background: SITE_COLORS[line.site] || "var(--accent)" }}
          />
          <span className="ticker-text">{t(line.verb, SITE_LABELS[line.site] || line.site)}</span>
        </div>
      ))}
    </div>
  );
}
