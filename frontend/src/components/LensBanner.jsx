import { useI18n } from "../i18n";

// The four-dot Google Lens glyph (brand-recognizable, drawn not fetched).
export function GoogleLensMark() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <circle cx="12" cy="12" r="3.4" fill="#4285f4" />
      <circle cx="12" cy="4.2" r="1.9" fill="#ea4335" />
      <circle cx="12" cy="19.8" r="1.9" fill="#34a853" />
      <circle cx="19.8" cy="12" r="1.9" fill="#fbbc05" />
      <circle cx="4.2" cy="12" r="1.9" fill="#9aa0a6" />
    </svg>
  );
}

// Which of the two things a Lens search found, so an "exact match" grid and a
// "nothing matched, here's what looks similar" grid are never mistaken for
// each other.
//
// `scope` picks the wording: Product Search narrows Lens hits to the stores the
// user selected, while Trending searches the whole web — where "on your
// selected sites" would be a lie.
const TEXT = {
  sites: {
    exact: ["lensExactTitle", "lensExactBody"],
    similar: ["lensSimilarTitle", "lensSimilarBody"],
  },
  web: {
    exact: ["lensExactTitle", "lensExactBodyWeb"],
    similar: ["lensSimilarTitleWeb", "lensSimilarBodyWeb"],
  },
};

export default function LensBanner({ mode, count, scope = "sites" }) {
  const { t } = useI18n();
  if (!mode || !count) return null;

  const [titleKey, bodyKey] = TEXT[scope][mode];
  return (
    <div className={`lens-banner lens-banner-${mode}`}>
      <GoogleLensMark />
      <span className="lens-banner-text">
        <strong>{t(titleKey)}</strong> {t(bodyKey, count)}
      </span>
    </div>
  );
}
