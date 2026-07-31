import { useState } from "react";
import "./App.css";
import SearchView from "./components/SearchView";
import TrendingView from "./components/TrendingView";
import BestSellersView from "./components/BestSellersView";
import WinningProducts from "./components/WinningProducts";
import CommandPalette from "./components/CommandPalette";
import AccessGate from "./components/AccessGate";
import { I18nProvider, LANGUAGES, useI18n } from "./i18n";
import { useSavedSearches, useRecentSearches, RUN_SEARCH_EVENT } from "./store";

function AppShell() {
  const [tab, setTab] = useState("bestsellers");
  const { t, lang, setLang } = useI18n();
  const { searches: savedSearches } = useSavedSearches();
  const { recent: recentSearches } = useRecentSearches();

  // Demo: Product Search (bestsellers) is the primary flow. The old sourcing
  // Search tab is hidden — its sources (Alibaba/AliExpress/Made-in-China) now
  // live behind the "Search for manufacturers" button. Files kept for easy
  // revert; add { id: "search", label: t("navSearch") } back to re-enable.
  const TABS = [
    { id: "bestsellers", label: t("navBestSellers") },
    { id: "winning", label: t("navWinning") },
    { id: "trending", label: t("navTrending") },
  ];

  function runSearch(query, sites) {
    setTab("bestsellers");
    // Defer so BestSellersView is mounted before the event fires.
    requestAnimationFrame(() =>
      window.dispatchEvent(new CustomEvent(RUN_SEARCH_EVENT, { detail: { query, sites } }))
    );
  }

  const paletteActions = [
    ...TABS.map((tb) => ({
      id: `nav-${tb.id}`,
      label: tb.label,
      hint: t("cpGoTo"),
      section: t("cpSectionNavigate"),
      run: () => setTab(tb.id),
    })),
    ...savedSearches.map((s) => ({
      id: `saved-${s.query}`,
      label: s.query,
      hint: `★ ${t("cpSavedSearch")}`,
      section: t("cpSectionSearches"),
      run: () => runSearch(s.query, s.sites),
    })),
    ...recentSearches
      .filter((r) => !savedSearches.some((s) => s.query === r.query))
      .map((r) => ({
        id: `recent-${r.query}`,
        label: r.query,
        hint: t("cpRecentSearch"),
        section: t("cpSectionSearches"),
        run: () => runSearch(r.query, r.sites),
      })),
  ];

  return (
    <div className="app">
      <nav className="navbar">
        <span className="navbar-brand">p2</span>
        <div className="navbar-links">
          {TABS.map((tb) => (
            <button
              key={tb.id}
              type="button"
              className={`navbar-link ${tab === tb.id ? "active" : ""}`}
              onClick={() => setTab(tb.id)}
            >
              {tb.label}
            </button>
          ))}
        </div>
        <div className="navbar-right">
          <button
            type="button"
            className="cmdk-hint"
            onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}
            title={t("cpOpenTitle")}
          >
            ⌘K
          </button>
          <div className="lang-switcher">
            {LANGUAGES.map((l) => (
              <button
                key={l.id}
                type="button"
                className={`lang-pill ${lang === l.id ? "active" : ""}`}
                onClick={() => setLang(l.id)}
              >
                {l.label}
              </button>
            ))}
          </div>
        </div>
      </nav>

      {tab === "search" && <SearchView />}
      {tab === "bestsellers" && <BestSellersView />}
      {tab === "winning" && <WinningProducts />}
      {tab === "trending" && (
        <div className="page">
          <TrendingView />
        </div>
      )}

      <CommandPalette actions={paletteActions} />
    </div>
  );
}

export default function App() {
  return (
    <I18nProvider>
      <AccessGate>
        <AppShell />
      </AccessGate>
    </I18nProvider>
  );
}
