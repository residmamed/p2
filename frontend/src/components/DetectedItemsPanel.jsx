import { cropUrl } from "../api";
import { useI18n } from "../i18n";

export default function DetectedItemsPanel({ items, selectedIds, onToggle, disabled }) {
  const { t } = useI18n();

  if (items.length === 0) {
    return <div className="status-message">{t("noItemsDetected")}</div>;
  }

  return (
    <div className="detected-items-grid">
      {items.map((item) => (
        <label key={item.crop_id} className={`detected-item ${selectedIds.has(item.crop_id) ? "selected" : ""}`}>
          <input
            type="checkbox"
            checked={selectedIds.has(item.crop_id)}
            onChange={() => onToggle(item.crop_id)}
            disabled={disabled}
          />
          <img src={cropUrl(item.crop_id)} alt="" />
        </label>
      ))}
    </div>
  );
}
