import { useEffect, useRef, useState } from "react";

// Two modes, because the two searches know different things about themselves.
//
// Estimated (`value` omitted): no backend endpoint reports progress for a
// single long request, so the fill is a smooth asymptotic guess that approaches
// — but never quite reaches — 92%, then jumps to 100% the moment the real
// result lands (loading flips false and this unmounts).
//
// Real (`value` given): the supplier search runs one request per product and
// they land one at a time, so the caller can count them. The bar is anchored on
// that count and creeps only partway into the gap the next completion will
// close — enough that it never looks frozen between products, never enough to
// claim progress that hasn't happened.
const ASYMPTOTE = 92;
const CREEP_SHARE = 0.4;
const CREEP_MS = 25000;

export default function ProgressBar({ label, detail, durationMs = 20000, value = null }) {
  const [pct, setPct] = useState(0);
  // Read inside the animation frame so a new count doesn't restart the effect
  // (which would reset the bar to zero on every product that lands).
  const valueRef = useRef(value);
  // When the real count last moved — the creep is measured from there, so each
  // completed product restarts it rather than letting one long drift carry the
  // bar to the top.
  const anchorRef = useRef(0);
  // A progress bar that goes backwards reads as a fault. It never can here, but
  // the guard is a line and the alternative is a bug that only shows up once
  // the counts arrive out of order.
  const peakRef = useRef(0);

  useEffect(() => {
    valueRef.current = value;
    anchorRef.current = performance.now();
  }, [value]);

  useEffect(() => {
    setPct(0);
    peakRef.current = 0;
    const start = performance.now();
    anchorRef.current = start;
    let raf;
    function tick(now) {
      const real = valueRef.current;
      const next =
        real == null
          ? ASYMPTOTE * (1 - Math.exp(-(now - start) / durationMs))
          : real + (100 - real) * CREEP_SHARE * (1 - Math.exp(-(now - anchorRef.current) / CREEP_MS));
      peakRef.current = Math.max(peakRef.current, next);
      setPct(peakRef.current);
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
      {detail && <div className="progress-detail">{detail}</div>}
    </div>
  );
}
