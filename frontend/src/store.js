// localStorage-backed app state for the workbench features (saved/recent
// searches, margin assumptions). The app has no backend database by design,
// so persistence is per-browser — same tradeoff as the language preference
// in i18n.jsx.
//
// useStoredState keeps every subscriber of the same key in sync via a custom
// window event, so e.g. a saved-search chip added in one component shows up
// immediately in the command palette built by another.

import { useCallback, useEffect, useRef, useState } from "react";

const SYNC_EVENT = "p2-store-sync";

// Fired (with { detail: { query, sites } }) by the command palette / app shell
// to ask BestSellersView to run a search. Lives here, not in App.jsx, so the
// view doesn't have to import from App (circular).
export const RUN_SEARCH_EVENT = "p2-run-search";

function readStored(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function useStoredState(key, initial) {
  const [value, setValue] = useState(() => readStored(key, initial));
  // Latest value, readable synchronously in set() — functional updates must
  // NOT be resolved inside the setState updater, because updaters run during
  // render and our persist+broadcast side effects would then setState on
  // other components mid-render (a React error).
  const valueRef = useRef(value);
  valueRef.current = value;

  useEffect(() => {
    function onSync(e) {
      if (e.detail?.key === key) {
        valueRef.current = e.detail.value;
        setValue(e.detail.value);
      }
    }
    window.addEventListener(SYNC_EVENT, onSync);
    return () => window.removeEventListener(SYNC_EVENT, onSync);
  }, [key]);

  const set = useCallback(
    (next) => {
      const resolved = typeof next === "function" ? next(valueRef.current) : next;
      valueRef.current = resolved;
      try {
        localStorage.setItem(key, JSON.stringify(resolved));
      } catch {
        // Quota/private-mode failure: state still works for this session.
      }
      setValue(resolved);
      // Notify other components on the same page (the browser's own
      // "storage" event only fires in *other* tabs).
      window.dispatchEvent(new CustomEvent(SYNC_EVENT, { detail: { key, value: resolved } }));
    },
    [key]
  );

  return [value, set];
}

// ---- Last search results (in memory, not persisted) -----------------------
//
// The rows Product Search last put on screen, shared so another tab can rank or
// summarise them without searching again. Deliberately NOT localStorage: a
// result set is hundreds of KB of listings that go stale the moment a store
// updates, and it belongs to this session only.
//
// This is what lets Winning Products be instant. It ranks what is already on
// screen rather than issuing its own store queries — which is both the correct
// behaviour (the board should describe *your* search) and the reason it costs
// nothing and takes no time.

let _lastResults = { query: "", sites: [], products: [], at: 0 };
const RESULTS_EVENT = "p2-results-changed";

export function publishSearchResults(query, sites, products) {
  _lastResults = { query, sites, products: products ?? [], at: Date.now() };
  window.dispatchEvent(new CustomEvent(RESULTS_EVENT));
}

export function useLastSearchResults() {
  const [value, setValue] = useState(_lastResults);
  useEffect(() => {
    // Re-read on mount as well as on the event: this component may mount long
    // after the search that filled the store, and would otherwise show the
    // empty initial value until the next search.
    setValue(_lastResults);
    const onChange = () => setValue(_lastResults);
    window.addEventListener(RESULTS_EVENT, onChange);
    return () => window.removeEventListener(RESULTS_EVENT, onChange);
  }, []);
  return value;
}

// ---- Saved + recent searches ---------------------------------------------

const SAVED_KEY = "p2_saved_searches";
const RECENT_KEY = "p2_recent_searches";
const RECENT_CAP = 8;

export function useSavedSearches() {
  const [searches, setSearches] = useStoredState(SAVED_KEY, []);

  const isSaved = useCallback((query) => searches.some((s) => s.query === query), [searches]);

  const save = useCallback(
    (query, sites = []) => {
      const q = query.trim();
      if (!q) return;
      setSearches((prev) =>
        prev.some((s) => s.query === q) ? prev : [{ query: q, sites, savedAt: Date.now() }, ...prev]
      );
    },
    [setSearches]
  );

  const remove = useCallback(
    (query) => setSearches((prev) => prev.filter((s) => s.query !== query)),
    [setSearches]
  );

  return { searches, isSaved, save, remove };
}

export function useRecentSearches() {
  const [recent, setRecent] = useStoredState(RECENT_KEY, []);

  const push = useCallback(
    (query, sites = []) => {
      const q = query.trim();
      if (!q) return;
      setRecent((prev) =>
        [{ query: q, sites, at: Date.now() }, ...prev.filter((s) => s.query !== q)].slice(0, RECENT_CAP)
      );
    },
    [setRecent]
  );

  return { recent, push };
}
