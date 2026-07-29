import { useEffect, useState } from "react";

// No backend endpoint reports real progress for these long-running requests
// (site scraping / actor calls), so the fill is a smooth asymptotic estimate
// that approaches — but never quite reaches — 92%, then jumps to 100% the
// moment the real result lands (loading flips false and this unmounts).
const ASYMPTOTE = 92;

export default function ProgressBar({ label, durationMs = 20000 }) {
  const [pct, setPct] = useState(0);

  useEffect(() => {
    setPct(0);
    const start = performance.now();
    let raf;
    function tick(now) {
      const elapsed = now - start;
      setPct(ASYMPTOTE * (1 - Math.exp(-elapsed / durationMs)));
      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [durationMs]);

  const rounded = Math.round(pct);

  return (
    <div className="progress-row">
      {label && <div className="progress-label">{label}</div>}
      <div className="progress-track-row">
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${rounded}%` }} />
        </div>
        <div className="progress-pct">{rounded}%</div>
      </div>
    </div>
  );
}
