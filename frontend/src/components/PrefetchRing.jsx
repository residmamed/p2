import { useI18n } from "../i18n";

// The supplier lookup that starts on its own the moment a product search lands,
// made visible.
//
// It was always running — it is the reason the "Search manufacturers" button
// comes back in seconds instead of minutes — but it reported nothing, so the
// only way to know whether the wait had already been paid was to press the
// button and find out. This is that answer, in the corner: a ring while it
// works, a tick and a count when the suppliers are already in hand.
//
// Deliberately small and out of the way. Nothing here is an action the user
// has to take — the work happens whether or not they look at it.
const R = 16;
const C = 2 * Math.PI * R;

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

export default function PrefetchRing({ pct, found, ready, onDismiss }) {
  const { t } = useI18n();
  const shown = Math.max(0, Math.min(100, Math.round(pct)));

  return (
    <div
      className={`prefetch-ring ${ready ? "prefetch-ring-ready" : ""}`}
      role="status"
      aria-live="polite"
      title={t("prefetchTitle")}
    >
      <div className="prefetch-ring-dial">
        <svg className="prefetch-ring-svg" viewBox="0 0 40 40" width="40" height="40" aria-hidden="true">
          <circle className="prefetch-ring-track" cx="20" cy="20" r={R} />
          <circle
            className="prefetch-ring-fill"
            cx="20"
            cy="20"
            r={R}
            style={{ strokeDasharray: C, strokeDashoffset: C * (1 - shown / 100) }}
          />
        </svg>
        <span className="prefetch-ring-value">
          {ready ? <CheckIcon /> : `${shown}%`}
        </span>
      </div>
      <span className="prefetch-ring-text">
        {ready
          ? found
            ? t("prefetchReady", found)
            : // Zero is a real answer, and the one worth saying out loud: the
              // button will come back empty in a moment, and knowing that now
              // is better than pressing it to find out.
              t("prefetchReadyNone")
          : t("prefetchWorking")}
      </span>
      <button
        type="button"
        className="prefetch-ring-close"
        onClick={onDismiss}
        aria-label={t("prefetchDismiss")}
      >
        ✕
      </button>
    </div>
  );
}
