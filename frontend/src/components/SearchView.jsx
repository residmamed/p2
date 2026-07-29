import { useRef, useState } from "react";
import SiteFilter from "./SiteFilter";
import ResultsGrid from "./ResultsGrid";
import ImageCropper from "./ImageCropper";
import ProgressBar from "./ProgressBar";
import { searchByText, searchByImage, searchViaLensExtension } from "../api";
import { SITES } from "../sites";
import { useI18n } from "../i18n";

function isAbortError(e) {
  return e?.name === "AbortError";
}

const PAGE_SIZE = 12;

export default function SearchView() {
  const { t } = useI18n();
  const [inputMode, setInputMode] = useState("text"); // 'text' | 'photo'
  const [query, setQuery] = useState("");
  const [rawPhoto, setRawPhoto] = useState(null);
  const [sites, setSites] = useState(SITES.map((s) => s.id));
  const [includeLens, setIncludeLens] = useState(true);

  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [products, setProducts] = useState([]);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [warnings, setWarnings] = useState([]);
  const [imagePreview, setImagePreview] = useState(null);
  const [lastSearch, setLastSearch] = useState(null); // { type, query, sites, page, hasMore }

  const fileInputRef = useRef(null);
  const abortControllerRef = useRef(null);

  function newAbortController() {
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    return controller;
  }

  function handleStop() {
    abortControllerRef.current?.abort();
    setLoading(false);
    setLoadingMore(false);
  }

  async function runSearch(searchFn, meta) {
    const controller = newAbortController();
    setLoading(true);
    setError(null);
    setHasSearched(true);
    setVisibleCount(PAGE_SIZE);
    try {
      const data = await searchFn(controller.signal);
      setProducts(data.results);
      setWarnings(data.warnings || []);
      setLastSearch({ ...meta, page: 1, hasMore: data.results.length > 0 });
    } catch (e) {
      if (isAbortError(e)) return;
      setError(e.message || "Something went wrong");
      setProducts([]);
      setWarnings([]);
      setLastSearch(null);
    } finally {
      setLoading(false);
    }
  }

  function handleTextSubmit(e) {
    e.preventDefault();
    if (!query.trim()) return;
    setImagePreview(null);
    const q = query.trim();
    runSearch((signal) => searchByText(q, { sites, signal }), { type: "text", query: q, sites });
  }

  function handleFileChosen(e) {
    const file = e.target.files?.[0];
    if (file) setRawPhoto(file);
    e.target.value = "";
  }

  async function handleCropConfirm(croppedFile) {
    setRawPhoto(null);
    setImagePreview(URL.createObjectURL(croppedFile));
    await runPhotoSearchLive(croppedFile);
  }

  // Photo search has two independent sources (the backend marketplace+Lens
  // call, and the browser-extension Lens bridge) — each renders as soon as it
  // lands instead of the page waiting for the slower one.
  async function runPhotoSearchLive(file) {
    const controller = newAbortController();
    setLoading(true);
    setError(null);
    setHasSearched(true);
    setVisibleCount(PAGE_SIZE);
    setProducts([]);
    setWarnings([]);
    setLastSearch({ type: "photo", sites, page: 1, hasMore: false });

    function addResults(results) {
      if (!results?.length) return;
      setProducts((prev) => [...prev, ...results]);
    }
    function addWarnings(warns) {
      if (!warns?.length) return;
      setWarnings((prev) => [...prev, ...warns]);
    }

    const tasks = [
      searchByImage(file, { sites, includeLens, signal: controller.signal })
        .then((r) => {
          addResults(r.results);
          addWarnings(r.warnings);
        })
        .catch((e) => {
          if (!isAbortError(e)) addWarnings([e.message || "Image search failed"]);
        }),
      searchViaLensExtension(file, { signal: controller.signal })
        .then((r) => {
          addResults(r.results);
          addWarnings(r.warnings);
        })
        .catch(() => {}),
    ];

    try {
      await Promise.all(tasks);
    } finally {
      setLoading(false);
    }
  }

  async function handleLoadMore() {
    if (!lastSearch) return;

    if (visibleCount < products.length) {
      setVisibleCount((c) => Math.min(products.length, c + PAGE_SIZE));
      return;
    }
    if (lastSearch.type !== "text" || !lastSearch.hasMore) return;

    const controller = newAbortController();
    setLoadingMore(true);
    try {
      const nextPage = lastSearch.page + 1;
      const data = await searchByText(lastSearch.query, {
        sites: lastSearch.sites,
        page: nextPage,
        signal: controller.signal,
      });
      if (data.results.length === 0) {
        setLastSearch((s) => ({ ...s, hasMore: false }));
      } else {
        setProducts((prev) => [...prev, ...data.results]);
        setWarnings((prev) => [...prev, ...(data.warnings || [])]);
        setVisibleCount((c) => c + PAGE_SIZE);
        setLastSearch((s) => ({ ...s, page: nextPage }));
      }
    } catch (e) {
      if (!isAbortError(e)) setError(e.message || "Something went wrong");
    } finally {
      setLoadingMore(false);
    }
  }

  const busy = loading;
  const hasMore = !!lastSearch && (visibleCount < products.length || lastSearch.hasMore);

  return (
    <div className="page">
      <h1 className="page-heading">{t("searchHeading")}</h1>
      <p className="page-subtitle">{t("searchSubtitle")}</p>

      <div className="pill-row">
        <button
          type="button"
          className={`pill ${inputMode === "text" ? "active" : ""}`}
          onClick={() => setInputMode("text")}
          disabled={busy}
        >
          {t("modeText")}
        </button>
        <button
          type="button"
          className={`pill ${inputMode === "photo" ? "active" : ""}`}
          onClick={() => setInputMode("photo")}
          disabled={busy}
        >
          {t("modePhoto")}
        </button>
      </div>

      {inputMode === "text" ? (
        <form onSubmit={handleTextSubmit}>
          <label className="field-label" htmlFor="sourcing-query">
            {t("whatSourcing")}
          </label>
          <input
            id="sourcing-query"
            className="text-input"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("queryPlaceholder")}
            disabled={busy}
            style={{ marginBottom: 24 }}
          />

          <span className="field-label">{t("sources")}</span>
          <SiteFilter selected={sites} onChange={setSites} disabled={busy} />

          <button type="submit" className="primary-button" disabled={busy || !query.trim()}>
            {t("search")}
          </button>
        </form>
      ) : (
        <div>
          <label className="field-label">{t("whatSourcing")}</label>
          {rawPhoto ? (
            <ImageCropper file={rawPhoto} onConfirm={handleCropConfirm} onCancel={() => setRawPhoto(null)} busy={busy} />
          ) : (
            <div className="photo-panel">
              <div className="photo-panel-empty">
                {t("uploadThenCrop")}
                <div>
                  <button
                    type="button"
                    className="primary-button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={busy}
                  >
                    {t("choosePhoto")}
                  </button>
                </div>
              </div>
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp,image/bmp,image/avif"
                ref={fileInputRef}
                onChange={handleFileChosen}
                style={{ display: "none" }}
              />
            </div>
          )}

          <span className="field-label">{t("sources")}</span>
          <SiteFilter selected={sites} onChange={setSites} disabled={busy} />

          <label className="checkbox-row" style={{ marginTop: 12 }}>
            <input
              type="checkbox"
              checked={includeLens}
              onChange={(e) => setIncludeLens(e.target.checked)}
              disabled={busy}
            />
            {t("includeLens")}
          </label>
        </div>
      )}

      {imagePreview && !loading && (
        <div style={{ marginBottom: 24 }}>
          <img src={imagePreview} alt="Search reference" style={{ maxHeight: 100, borderRadius: 10, border: "1px solid var(--border)" }} />
        </div>
      )}

      {loading && (
        <>
          <ProgressBar
            label={inputMode === "photo" && includeLens ? t("searchingLabelLens") : t("searchingLabel")}
            durationMs={inputMode === "photo" && includeLens ? 150000 : 30000}
          />
          <div className="load-more-row">
            <button type="button" className="secondary-button" onClick={handleStop}>
              {t("stopSearch")}
            </button>
          </div>
        </>
      )}

      {error && <div className="status-message error">{error}</div>}

      {!loading && !error && warnings.length > 0 && (
        <div className="status-message warning">
          {warnings.map((w, i) => (
            <div key={i}>{w}</div>
          ))}
        </div>
      )}

      {!loading && !error && hasSearched && products.length === 0 && warnings.length === 0 && (
        <div className="status-message">{t("noResults")}</div>
      )}

      <ResultsGrid
        products={products.slice(0, visibleCount)}
        hasMore={!loading && hasMore}
        onLoadMore={handleLoadMore}
        loadingMore={loadingMore}
      />
    </div>
  );
}
