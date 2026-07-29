import { useRef, useState } from "react";
import TrendingForm from "./TrendingForm";
import InspirationGrid from "./InspirationGrid";
import DetectedItemsPanel from "./DetectedItemsPanel";
import SiteFilter from "./SiteFilter";
import ResultsGrid from "./ResultsGrid";
import ProgressBar from "./ProgressBar";
import ImageCropper from "./ImageCropper";
import LensBanner from "./LensBanner";
import {
  searchPinterest,
  detectItems,
  detectItemsFromUpload,
  fetchInspirationImageAsFile,
  fetchCropAsFile,
  searchByImage,
  searchGoogleLens,
  searchViaLensExtension,
  selectLensMatches,
} from "../api";
import { SITES } from "../sites";
import { useI18n } from "../i18n";

function isAbortError(e) {
  return e?.name === "AbortError";
}

const PAGE_SIZE = 12;

export default function TrendingView() {
  const { t } = useI18n();
  const [images, setImages] = useState([]);
  const [selectedImage, setSelectedImage] = useState(null);
  const [cropSourceFile, setCropSourceFile] = useState(null);
  const [detectedItems, setDetectedItems] = useState([]);
  const [selectedItemIds, setSelectedItemIds] = useState(new Set());
  const [sites, setSites] = useState(SITES.map((s) => s.id));

  const [loadingIdea, setLoadingIdea] = useState(false);
  const [loadingDetect, setLoadingDetect] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState(null);

  const [products, setProducts] = useState([]);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [warnings, setWarnings] = useState([]);
  const [hasSearched, setHasSearched] = useState(false);
  // What Google Lens found, same as Product Search: mode is null, "exact"
  // (pixel-identical pages) or "similar" (closest visual matches). The count is
  // tracked here rather than derived from `products`, which also holds the
  // supplier-search hits — those carry an image_match of their own and would
  // inflate it.
  const [lens, setLens] = useState({ mode: null, count: 0 });

  const uploadInputRef = useRef(null);
  const abortControllerRef = useRef(null);

  function newAbortController() {
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    return controller;
  }

  function handleStopSearch() {
    abortControllerRef.current?.abort();
    setSearching(false);
  }

  function resetDetection() {
    setDetectedItems([]);
    setSelectedItemIds(new Set());
    setProducts([]);
    setVisibleCount(PAGE_SIZE);
    setWarnings([]);
    setHasSearched(false);
    setLens({ mode: null, count: 0 });
  }

  async function handleIdeaSearch(idea) {
    setLoadingIdea(true);
    setError(null);
    setImages([]);
    setSelectedImage(null);
    setCropSourceFile(null);
    resetDetection();
    try {
      const data = await searchPinterest(idea);
      setImages(data.images);
    } catch (e) {
      setError(e.message || "Pinterest search failed");
    } finally {
      setLoadingIdea(false);
    }
  }

  async function handleImageSelect(image) {
    setSelectedImage(image);
    setCropSourceFile(null);
    resetDetection();
    setLoadingDetect(true);
    setError(null);

    // Best-effort: lets the user crop this image manually. Runs alongside
    // detection rather than blocking on it — a failure here shouldn't stop
    // the (more important) auto-detect path from showing its own results.
    fetchInspirationImageAsFile(image.image_url).then(setCropSourceFile).catch(() => {});

    try {
      const data = await detectItems(image.image_url);
      setDetectedItems(data.items);
    } catch (e) {
      setError(e.message || "Item detection failed");
    } finally {
      setLoadingDetect(false);
    }
  }

  async function handleUploadDetect(file) {
    setSelectedImage(null);
    setCropSourceFile(file);
    resetDetection();
    setLoadingDetect(true);
    setError(null);
    try {
      const data = await detectItemsFromUpload(file);
      setDetectedItems(data.items);
    } catch (e) {
      setError(e.message || "Item detection failed");
    } finally {
      setLoadingDetect(false);
    }
  }

  function handleUploadFileChosen(e) {
    const file = e.target.files?.[0];
    if (file) handleUploadDetect(file);
    e.target.value = "";
  }

  function toggleItem(cropId) {
    setSelectedItemIds((prev) => {
      const next = new Set(prev);
      if (next.has(cropId)) next.delete(cropId);
      else next.add(cropId);
      return next;
    });
  }

  async function runSearchOnFiles(fileEntries) {
    // fileEntries: [{ file, detectedItem }]
    const controller = newAbortController();
    setSearching(true);
    setError(null);
    setHasSearched(true);
    setVisibleCount(PAGE_SIZE);
    setProducts([]);
    setWarnings([]);
    setLens({ mode: null, count: 0 });

    function addResults(results) {
      if (!results?.length) return;
      setProducts((prev) => [...prev, ...results]);
    }
    function addWarnings(warns) {
      if (!warns?.length) return;
      setWarnings((prev) => [...prev, ...warns]);
    }

    // Each source renders as soon as it lands instead of the page waiting for
    // the slowest one — Google Lens (Apify + the browser-extension bridge)
    // goes first per file since it's the slower, higher-signal source; the
    // faster marketplace scrapers queue right behind it.
    const tasks = [];
    for (const { file, detectedItem } of fileEntries) {
      const provenance = { detectedItem, inspirationImageUrl: selectedImage?.image_url };

      tasks.push(
        searchGoogleLens(file, { ...provenance, signal: controller.signal })
          .then((r) => {
            // Same treatment Product Search gives a Lens response: keep the
            // exact matches re-tagged to the retail site they live on, or fall
            // back to the closest visual ones — instead of dumping all 65 raw
            // hits, social pages and duplicates included, into the grid.
            //
            // No site allowlist here: this page's filter picks *supplier*
            // sites, which a retail Lens hit never matches.
            const { results, lensMode } = selectLensMatches(r.results || []);
            addResults(results);
            addWarnings(r.warnings);
            // Several crops can be searched in one run, so accumulate rather
            // than overwrite. "similar" only stands while nothing found an
            // exact match — one exact hit is the better answer for the page.
            if (lensMode) {
              setLens((prev) => ({
                mode: prev.mode === "exact" ? "exact" : lensMode,
                count: prev.count + results.length,
              }));
            }
          })
          .catch((e) => {
            if (!isAbortError(e)) addWarnings([e.message || "Google Lens search failed"]);
          })
      );
      tasks.push(
        searchViaLensExtension(file, { signal: controller.signal })
          .then((r) => {
            addResults(r.results);
            addWarnings(r.warnings);
          })
          .catch(() => {})
      );
      tasks.push(
        searchByImage(file, { sites, ...provenance, signal: controller.signal })
          .then((r) => {
            addResults(r.results);
            addWarnings(r.warnings);
          })
          .catch((e) => {
            if (!isAbortError(e)) addWarnings([e.message || "Image search failed"]);
          })
      );
    }

    try {
      await Promise.all(tasks);
    } finally {
      setSearching(false);
    }
  }

  async function handleSearchSelected() {
    const selectedItems = detectedItems.filter((it) => selectedItemIds.has(it.crop_id));
    if (selectedItems.length === 0) return;
    const fileEntries = await Promise.all(
      selectedItems.map(async (item) => ({ file: await fetchCropAsFile(item.crop_id), detectedItem: item.label }))
    );
    runSearchOnFiles(fileEntries);
  }

  function handleManualCropConfirm(croppedFile) {
    runSearchOnFiles([{ file: croppedFile, detectedItem: null }]);
  }

  function handleLoadMore() {
    setVisibleCount((c) => Math.min(products.length, c + PAGE_SIZE));
  }

  const busy = loadingIdea || loadingDetect || searching;
  const hasMore = visibleCount < products.length;

  return (
    <div>
      <h1 className="page-heading">{t("trendingHeading")}</h1>
      <p className="page-subtitle">{t("trendingSubtitle")}</p>

      <TrendingForm onSearch={handleIdeaSearch} disabled={busy} />

      {loadingIdea && <ProgressBar label={t("findingInspiration")} durationMs={12000} />}
      {error && <div className="status-message error">{error}</div>}

      <InspirationGrid
        images={images}
        selectedImageUrl={selectedImage?.image_url}
        onSelect={handleImageSelect}
        disabled={busy}
      />

      <div className="upload-own-row">
        <button
          type="button"
          className="secondary-button"
          onClick={() => uploadInputRef.current?.click()}
          disabled={busy}
        >
          {t("orUploadOwnPhoto")}
        </button>
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp,image/bmp,image/avif"
          ref={uploadInputRef}
          onChange={handleUploadFileChosen}
          style={{ display: "none" }}
        />
      </div>

      {cropSourceFile && (
        <>
          <h3 className="section-heading">{t("orCropManually")}</h3>
          <ImageCropper file={cropSourceFile} onConfirm={handleManualCropConfirm} onCancel={() => setCropSourceFile(null)} busy={searching} />
        </>
      )}

      {loadingDetect && <ProgressBar label={t("detectingItems")} durationMs={6000} />}

      {(selectedImage || cropSourceFile) && !loadingDetect && detectedItems.length > 0 && (
        <>
          <h3 className="section-heading">{t("selectItemsToSearch")}</h3>
          <DetectedItemsPanel
            items={detectedItems}
            selectedIds={selectedItemIds}
            onToggle={toggleItem}
            disabled={searching}
          />
          <span className="field-label">{t("sources")}</span>
          <SiteFilter selected={sites} onChange={setSites} disabled={searching} />
          <button
            type="button"
            className="primary-button"
            onClick={handleSearchSelected}
            disabled={searching || selectedItemIds.size === 0}
          >
            {t("searchSelected", selectedItemIds.size)}
          </button>
        </>
      )}

      {searching && (
        <>
          <ProgressBar label={t("searchingSelected")} durationMs={30000} />
          <div className="load-more-row">
            <button type="button" className="secondary-button" onClick={handleStopSearch}>
              {t("stopSearch")}
            </button>
          </div>
        </>
      )}

      {!searching && warnings.length > 0 && (
        <div className="status-message warning">
          {warnings.map((w, i) => (
            <div key={i}>{w}</div>
          ))}
        </div>
      )}

      {!searching && hasSearched && products.length === 0 && warnings.length === 0 && (
        <div className="status-message">{t("noResults")}</div>
      )}

      {!searching && (
        <LensBanner mode={lens.mode} count={lens.count} scope="web" />
      )}

      <ResultsGrid
        products={products.slice(0, visibleCount)}
        hasMore={!searching && hasMore}
        onLoadMore={handleLoadMore}
      />
    </div>
  );
}
