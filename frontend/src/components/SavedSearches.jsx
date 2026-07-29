import { useSavedSearches, useRecentSearches } from "../store";
import { useI18n } from "../i18n";
import "./SavedSearches.css";

// Saved + recent search chips shown under the search form. Clicking a chip
// re-runs the search via onRun(query, sites); the parent records recents.
export default function SavedSearches({ currentQuery, currentSites, onRun }) {
  const { t } = useI18n();
  const { searches, isSaved, save, remove } = useSavedSearches();
  const { recent } = useRecentSearches();

  const query = (currentQuery || "").trim();
  const visibleRecent = recent.filter((r) => !isSaved(r.query));

  if (!searches.length && !visibleRecent.length && !query) return null;

  return (
    <div className="ss-wrap">
      {searches.length > 0 && (
        <div className="ss-row">
          <span className="ss-row-label">{t("ssSaved")}</span>
          {searches.map((s) => (
            <span key={s.query} className="ss-chip">
              <button
                type="button"
                className="ss-chip-main"
                onClick={() => onRun(s.query, s.sites)}
              >
                <span className="ss-chip-star" aria-hidden="true">★</span>
                {s.query}
              </button>
              <button
                type="button"
                className="ss-chip-remove"
                aria-label={t("ssRemove")}
                onClick={(e) => {
                  e.stopPropagation();
                  remove(s.query);
                }}
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      )}
      {visibleRecent.length > 0 && (
        <div className="ss-row">
          <span className="ss-row-label">{t("ssRecent")}</span>
          {visibleRecent.map((r) => (
            <button
              key={r.query}
              type="button"
              className="ss-chip ss-chip-button"
              onClick={() => onRun(r.query, r.sites)}
            >
              {r.query}
            </button>
          ))}
        </div>
      )}
      {query &&
        (isSaved(query) ? (
          <div className="ss-row">
            <button type="button" className="ss-ghost ss-ghost-saved" onClick={() => remove(query)}>
              <span aria-hidden="true">★</span> {t("ssSavedBadge")}
            </button>
          </div>
        ) : (
          <div className="ss-row">
            <button
              type="button"
              className="ss-ghost"
              onClick={() => save(currentQuery, currentSites)}
            >
              <span aria-hidden="true">☆</span> {t("ssSaveCurrent")}
            </button>
          </div>
        ))}
    </div>
  );
}
