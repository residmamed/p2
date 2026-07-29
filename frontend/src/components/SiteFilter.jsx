import { SITES } from "../sites";

export default function SiteFilter({ selected, onChange, disabled, sites = SITES }) {
  function toggle(siteId) {
    if (selected.includes(siteId)) {
      // Selection is optional — deselecting everything means "all sources".
      onChange(selected.filter((id) => id !== siteId));
    } else {
      onChange([...selected, siteId]);
    }
  }

  return (
    <div className="pill-row">
      {sites.map((site) => (
        <button
          key={site.id}
          type="button"
          className={`pill ${selected.includes(site.id) ? "active" : ""}`}
          onClick={() => toggle(site.id)}
          disabled={disabled}
        >
          {site.label}
        </button>
      ))}
    </div>
  );
}
