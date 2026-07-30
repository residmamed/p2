import { useEffect, useRef, useState } from "react";
import { SITES } from "../sites";
import { useI18n } from "../i18n";

export default function SiteFilter({ selected, onChange, disabled, sites = SITES }) {
  const { t } = useI18n();
  // Which roadmap store was last tapped, so the answer names it rather than
  // leaving the user to wonder which pill ignored them.
  const [pending, setPending] = useState(null);
  const timerRef = useRef(null);

  useEffect(() => () => clearTimeout(timerRef.current), []);

  function announceComingSoon(site) {
    clearTimeout(timerRef.current);
    setPending(site.label);
    timerRef.current = setTimeout(() => setPending(null), 4000);
  }

  function toggle(siteId) {
    if (selected.includes(siteId)) {
      onChange(selected.filter((id) => id !== siteId));
    } else {
      onChange([...selected, siteId]);
    }
  }

  return (
    <div className="site-filter">
      <div className="pill-row">
        {sites.map((site) =>
          site.comingSoon ? (
            // Neither `disabled` nor aria-disabled: both make the pill inert to
            // a click, and this one exists precisely to answer one. Its title
            // carries the same sentence the note does, so hover and assistive
            // tech get the answer without having to tap.
            <button
              key={site.id}
              type="button"
              className={`pill pill-soon ${disabled ? "pill-soon-busy" : ""}`}
              onClick={() => announceComingSoon(site)}
              title={t("comingSoonNote", site.label)}
            >
              {site.label}
              <span className="pill-soon-tag">{t("comingSoonShort")}</span>
            </button>
          ) : (
            <button
              key={site.id}
              type="button"
              className={`pill ${selected.includes(site.id) ? "active" : ""}`}
              onClick={() => toggle(site.id)}
              disabled={disabled}
            >
              {site.label}
            </button>
          )
        )}
      </div>
      {pending && (
        <div className="site-filter-note" role="status">
          {t("comingSoonNote", pending)}
        </div>
      )}
    </div>
  );
}
