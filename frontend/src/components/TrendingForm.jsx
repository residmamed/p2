import { useState } from "react";
import { useI18n } from "../i18n";

export default function TrendingForm({ onSearch, disabled }) {
  const { t } = useI18n();
  const [idea, setIdea] = useState("");

  function handleSubmit(e) {
    e.preventDefault();
    if (idea.trim()) onSearch(idea.trim());
  }

  return (
    <form onSubmit={handleSubmit}>
      <label className="field-label" htmlFor="trending-idea">
        {t("ideaLabel")}
      </label>
      <input
        id="trending-idea"
        className="text-input"
        type="text"
        value={idea}
        onChange={(e) => setIdea(e.target.value)}
        placeholder={t("ideaPlaceholder")}
        disabled={disabled}
        style={{ marginBottom: 16 }}
      />
      <button type="submit" className="primary-button" disabled={disabled || !idea.trim()} style={{ marginBottom: 28 }}>
        {t("findInspiration")}
      </button>
    </form>
  );
}
