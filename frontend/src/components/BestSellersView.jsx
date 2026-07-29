import { useEffect, useMemo, useRef, useState } from "react";
import SiteFilter from "./SiteFilter";
import ProductCard from "./ProductCard";
import ProgressBar from "./ProgressBar";
import ImageCropper from "./ImageCropper";
import MarketSnapshot from "./MarketSnapshot";
import ResultsToolbar, { applyResultFilters, DEFAULT_FILTERS } from "./ResultsToolbar";
import SavedSearches from "./SavedSearches";
import LensBanner from "./LensBanner";
import { exportProductsToExcel } from "../exportExcel";
import { searchBestSellers, searchBestSellersByImage, searchManufacturers, PRODUCT_SEARCH_MS, MFR_SEARCH_MS } from "../api";
import { BESTSELLER_SITES, MANUFACTURER_SITES, SITE_LABELS, SITE_COLORS } from "../sites";
import { useI18n } from "../i18n";
import { useRecentSearches, RUN_SEARCH_EVENT } from "../store";

function isAbortError(e) {
  return e?.name === "AbortError";
}

// Supplier rating shown as 5 stars (rounded to the nearest half) plus the
// numeric value. No rating → "N/A".
function StarRating({ value }) {
  if (value == null) return <span className="mfr-rating mfr-rating-na">N/A</span>;
  const rounded = Math.round(value * 2) / 2;
  return (
    <span className="mfr-rating" title={`${value.toFixed(1)} / 5`}>
      <span className="mfr-stars" aria-hidden="true">
        {[1, 2, 3, 4, 5].map((i) => {
          const fill = Math.max(0, Math.min(1, rounded - (i - 1)));
          return (
            <span key={i} className="mfr-star">
              <span className="mfr-star-fill" style={{ width: `${fill * 100}%` }}>
                ★
              </span>
              ☆
            </span>
          );
        })}
      </span>
      <span className="mfr-rating-num">{value.toFixed(1)}</span>
    </span>
  );
}

// How much this supplier row can be trusted to be the same product as the photo
// it was found with. Two things have to be visible, because they are different
// claims: the tier, and whether anything actually looked at the two pictures.
// A "similar" that a vision model judged and a "similar" that only shares a
// perceptual-hash bucket read identically otherwise, and they are not the same
// evidence — showing only the tier is how a guess starts looking like a match.
function MatchBadge({ supplier, t }) {
  const tier = supplier.match_tier;
  if (!tier) return <span className="mfr-match mfr-match-na">{t("matchUnknown")}</span>;

  const verified = supplier.match_basis === "vision";
  const percent =
    supplier.match_confidence != null ? Math.round(supplier.match_confidence * 100) : null;
  // The note is the model's own reason; the fallback names the weaker method
  // rather than leaving an unexplained label.
  const title = verified
    ? [supplier.match_note, percent != null ? `${percent}% confident` : null]
        .filter(Boolean)
        .join(" — ") || t("matchByVision")
    : t("matchByHash");

  return (
    <span className={`mfr-match mfr-match-${tier}`} title={title}>
      <span className="mfr-match-tier">{t(`matchTier_${tier}`)}</span>
      {verified && (
        <span className="mfr-match-verified" aria-label={t("matchByVision")}>
          ✓
        </span>
      )}
    </span>
  );
}

function MailIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="m22 7-10 6L2 7" />
    </svg>
  );
}

function ChatIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z" />
    </svg>
  );
}
function WhatsAppIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true">
      <path d="M17.5 14.4c-.3-.2-1.7-.9-2-1-.3-.1-.5-.1-.6.2-.2.3-.7.9-.8 1-.2.2-.3.2-.6.1-.3-.2-1.2-.5-2.3-1.4-.9-.8-1.4-1.7-1.6-2-.2-.3 0-.5.1-.6l.5-.5c.1-.2.2-.3.3-.5 0-.2 0-.4 0-.5 0-.2-.6-1.5-.9-2-.2-.5-.4-.4-.6-.4h-.5c-.2 0-.5.1-.7.3-.3.3-1 .9-1 2.3s1 2.7 1.2 2.9c.1.2 2 3.1 5 4.3.7.3 1.2.5 1.6.6.7.2 1.3.2 1.8.1.6-.1 1.7-.7 1.9-1.4.2-.7.2-1.2.2-1.4-.1-.1-.3-.2-.6-.4zM12 2a10 10 0 0 0-8.6 15l-1.3 4.7 4.8-1.3A10 10 0 1 0 12 2zm0 18.2c-1.5 0-3-.4-4.2-1.1l-.3-.2-2.9.8.8-2.8-.2-.3A8.2 8.2 0 1 1 12 20.2z" />
    </svg>
  );
}
function StoreIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 9 4.5 4h15L21 9M4 9v10h16V9M4 9h16M9 19v-5h6v5" />
    </svg>
  );
}
function SparkleIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
      <path d="M12 2l1.9 5.1L19 9l-5.1 1.9L12 16l-1.9-5.1L5 9l5.1-1.9L12 2zM19 14l.9 2.4L22 17l-2.1.9L19 20l-.9-2.1L16 17l2.1-.6L19 14zM5 15l.7 1.8L7.5 17.5l-1.8.7L5 20l-.7-1.8L2.5 17.5l1.8-.7L5 15z" />
    </svg>
  );
}

// The channels a supplier physically supports.
function supportsOf(s) {
  return { email: !!s.email, whatsapp: !!s.contact_value, sms: !!s.contact_value, platform: true };
}
// Given the user's enabled send methods, pick the channel actually used for a
// supplier (priority order). Platform inbox is the universal fallback, so every
// supplier stays reachable as long as at least one supported method is enabled.
const METHOD_PRIORITY = ["email", "whatsapp", "sms", "platform"];
function resolveChannel(s, methods) {
  const supports = supportsOf(s);
  for (const m of METHOD_PRIORITY) {
    if (!methods[m] || !supports[m]) continue;
    if (m === "email") return { kind: "email", value: s.email };
    if (m === "whatsapp") return { kind: "whatsapp", value: s.phone };
    if (m === "sms") return { kind: "sms", value: s.phone };
    if (m === "platform") return { kind: "platform", value: SITE_LABELS[s.site] || s.site };
  }
  return { kind: "none", value: null };
}
const CHANNEL_ICON = {
  email: <MailIcon />,
  whatsapp: <WhatsAppIcon />,
  sms: <ChatIcon />,
  platform: <StoreIcon />,
};
const SEND_METHODS = [
  { key: "email", labelKey: "methodEmail", icon: <MailIcon /> },
  { key: "whatsapp", labelKey: "methodWhatsapp", icon: <WhatsAppIcon /> },
  { key: "sms", labelKey: "methodSms", icon: <ChatIcon /> },
  { key: "platform", labelKey: "methodPlatform", icon: <StoreIcon /> },
];

// A tailored outreach draft — the "AI" writer. Deterministic, references the
// actual product and supplier count so it reads bespoke.
function buildAiDraft(list, t) {
  const product = list[0]?._product || t("msgYourProduct");
  const short = product.length > 60 ? product.slice(0, 57) + "…" : product;
  return (
    `Hello,\n\n` +
    `We came across your listing for "${short}" and were impressed by your catalog. ` +
    `Our team is sourcing this line in bulk and would love to work with you.\n\n` +
    `Could you please share:\n` +
    `• Your best wholesale price at 500–2,000 pcs\n` +
    `• MOQ, lead time, and current stock\n` +
    `• Whether OEM/ODM and samples are available\n\n` +
    `We're comparing a shortlist of ${list.length} suppliers this week and will move quickly. ` +
    `Looking forward to your best offer.\n\n` +
    `Best regards,\nSourcing Team`
  );
}

// Compose + send modal for the checked suppliers. Lets the user pick which
// channels to send over, AI-draft the message, then runs a mock send.
function MessageModal({ recipients, onClose, onSent, t }) {
  // Freeze the recipient list for the modal's lifetime — the parent clears the
  // selection on send, which would otherwise empty this out mid-view.
  const [list] = useState(recipients);
  const [message, setMessage] = useState("");
  const [methods, setMethods] = useState({ email: true, whatsapp: true, sms: false, platform: true });
  const [drafting, setDrafting] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const draftTimer = useRef(null);

  useEffect(() => () => clearInterval(draftTimer.current), []);

  const resolved = list.map((r) => ({ r, ch: resolveChannel(r, methods) }));
  const reachable = resolved.filter((x) => x.ch.kind !== "none");
  const counts = reachable.reduce((acc, x) => {
    acc[x.ch.kind] = (acc[x.ch.kind] || 0) + 1;
    return acc;
  }, {});
  const anyMethod = SEND_METHODS.some((m) => methods[m.key]);

  function toggleMethod(key) {
    setMethods((m) => ({ ...m, [key]: !m[key] }));
  }

  // "AI" writer with a live typewriter reveal — the creative flourish.
  function handleAiDraft() {
    const text = buildAiDraft(list, t);
    clearInterval(draftTimer.current);
    setDrafting(true);
    setMessage("");
    let i = 0;
    draftTimer.current = setInterval(() => {
      i += 3;
      setMessage(text.slice(0, i));
      if (i >= text.length) {
        clearInterval(draftTimer.current);
        setMessage(text);
        setDrafting(false);
      }
    }, 12);
  }

  async function handleSend() {
    if (!message.trim() || !reachable.length) return;
    setSending(true);
    await new Promise((r) => setTimeout(r, 1300)); // simulate delivery
    setSending(false);
    setSent(true);
    onSent(); // clear the parent's selection
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="msg-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        {sent ? (
          <div className="msg-sent">
            <div className="msg-sent-check" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 6 9 17l-5-5" />
              </svg>
            </div>
            <h3 className="msg-title">{t("msgSentTitle", reachable.length)}</h3>
            <p className="msg-sent-sub">
              {[
                counts.email > 0 && t("msgViaEmail", counts.email),
                counts.whatsapp > 0 && t("msgViaWhatsapp", counts.whatsapp),
                counts.sms > 0 && t("msgViaSms", counts.sms),
                counts.platform > 0 && t("msgViaInbox", counts.platform),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            <button type="button" className="primary-button" onClick={onClose}>
              {t("msgDone")}
            </button>
          </div>
        ) : (
          <>
            <div className="msg-modal-head">
              <h3 className="msg-title">{t("msgTitle", list.length)}</h3>
              <button type="button" className="msg-close" onClick={onClose} aria-label={t("msgCancel")}>
                ✕
              </button>
            </div>

            {/* Send method picker */}
            <span className="field-label">{t("msgSendVia")}</span>
            <div className="msg-methods">
              {SEND_METHODS.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  className={`msg-method ${methods[m.key] ? "active" : ""} msg-method-${m.key}`}
                  onClick={() => toggleMethod(m.key)}
                  aria-pressed={methods[m.key]}
                >
                  {m.icon}
                  {t(m.labelKey)}
                </button>
              ))}
            </div>

            <div className="msg-recipients">
              {resolved.map(({ r, ch }) => (
                <span key={r._key} className={`msg-chip ${ch.kind === "none" ? "msg-chip-off" : ""}`}>
                  <span className="msg-chip-name">{r.seller_name}</span>
                  <span className={`msg-chip-ch msg-chip-ch-${ch.kind}`}>
                    {ch.kind === "none" ? <StoreIcon /> : CHANNEL_ICON[ch.kind]}
                    {ch.kind === "platform"
                      ? t("msgViaSite", ch.value)
                      : ch.kind === "none"
                      ? t("msgUnreachable")
                      : ch.value}
                  </span>
                </span>
              ))}
            </div>

            <div className="msg-label-row">
              <label className="field-label" htmlFor="msg-body">
                {t("msgLabel")}
              </label>
              <button
                type="button"
                className={`msg-ai-draft ${drafting ? "drafting" : ""}`}
                onClick={handleAiDraft}
                disabled={sending || drafting}
              >
                <SparkleIcon />
                {drafting ? t("msgAiDrafting") : t("msgAiDraft")}
              </button>
            </div>
            <textarea
              id="msg-body"
              className="msg-textarea"
              rows={6}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder={t("msgPlaceholder")}
              disabled={sending || drafting}
              autoFocus
            />

            <div className="msg-modal-foot">
              <span className="msg-reach-note">
                {anyMethod ? t("msgReachSummary", reachable.length, list.length) : t("msgPickMethod")}
              </span>
              <div className="msg-foot-actions">
                <button type="button" className="secondary-button" onClick={onClose} disabled={sending}>
                  {t("msgCancel")}
                </button>
                <button
                  type="button"
                  className="primary-button"
                  onClick={handleSend}
                  disabled={sending || drafting || !message.trim() || !reachable.length}
                >
                  {sending ? t("msgSending") : t("msgSend", reachable.length)}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function CameraIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
      <circle cx="12" cy="13" r="4" />
    </svg>
  );
}

export default function BestSellersView() {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [rawPhoto, setRawPhoto] = useState(null);
  const [imagePreview, setImagePreview] = useState(null);
  const [sites, setSites] = useState([]); // no store selected by default

  // Product search state
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [products, setProducts] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [lensMode, setLensMode] = useState(null); // null | "exact" | "similar"
  const [searchMode, setSearchMode] = useState("text"); // drives the loading label
  const [selectedIds, setSelectedIds] = useState(() => new Set());

  // Manufacturer search state
  const [mfrLoading, setMfrLoading] = useState(false);
  const [mfrGroups, setMfrGroups] = useState(null); // null = not searched yet
  const [mfrWarnings, setMfrWarnings] = useState([]);
  const [mfrSites, setMfrSites] = useState(MANUFACTURER_SITES.map((s) => s.id));

  // Suppliers checked for messaging (keyed `${productId}-${index}`) + modal.
  const [checkedSuppliers, setCheckedSuppliers] = useState(() => new Set());
  const [messageOpen, setMessageOpen] = useState(false);

  // Workbench state: refine/sort filters.
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const { push: pushRecent } = useRecentSearches();

  const abortRef = useRef(null);
  const mfrAnchorRef = useRef(null);
  const fileInputRef = useRef(null);

  function newAbort() {
    abortRef.current?.abort();
    const c = new AbortController();
    abortRef.current = c;
    return c;
  }

  // Shared product-search runner for both the text and photo entry points.
  // `mode` is "text" or "lens"; a lens search reports back exact/similar via
  // data.lensMode so the UI can explain what Google Lens found.
  async function runProductSearch(searchFn, mode = "text") {
    const controller = newAbort();
    setSearchMode(mode);
    setLoading(true);
    setError(null);
    setHasSearched(true);
    setSelectedIds(new Set());
    setMfrGroups(null);
    setMfrWarnings([]);
    setLensMode(null);
    setFilters(DEFAULT_FILTERS); // stale refinements don't apply to a new set
    try {
      const data = await searchFn(controller.signal);
      setProducts(data.results);
      setWarnings(data.warnings || []);
      if (mode === "lens") setLensMode(data.lensMode || "similar");
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err.message || "Something went wrong");
      setProducts([]);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    if (!query.trim()) return;
    const q = query.trim();
    setImagePreview(null); // a text search supersedes any photo reference
    pushRecent(q, sites);
    runProductSearch((signal) => searchBestSellers(q, { sites, signal }));
  }

  // Saved-search chips and the command palette re-run searches through here.
  function runNamedSearch(q, siteList) {
    setQuery(q);
    setImagePreview(null);
    const nextSites = siteList || [];
    setSites(nextSites);
    pushRecent(q, nextSites);
    runProductSearch((signal) => searchBestSellers(q, { sites: nextSites, signal }));
  }
  const runNamedSearchRef = useRef(runNamedSearch);
  runNamedSearchRef.current = runNamedSearch;

  useEffect(() => {
    function onRunSearch(e) {
      const { query: q, sites: s } = e.detail || {};
      if (q) runNamedSearchRef.current(q, s);
    }
    window.addEventListener(RUN_SEARCH_EVENT, onRunSearch);
    return () => window.removeEventListener(RUN_SEARCH_EVENT, onRunSearch);
  }, []);

  function handleFileChosen(e) {
    const file = e.target.files?.[0];
    if (file) setRawPhoto(file);
    e.target.value = "";
  }

  function handleCropConfirm(croppedFile) {
    setRawPhoto(null);
    setImagePreview(URL.createObjectURL(croppedFile));
    runProductSearch(
      (signal) => searchBestSellersByImage(croppedFile, { sites, signal }),
      "lens"
    );
  }

  // The backend Product model has no `id` — that field only ever existed on the
  // old mock data. Keying selection on product.id therefore stored `undefined`,
  // and `selectedIds.has(undefined)` matched EVERY card, so picking one product
  // selected all of them. product_url is unique per listing and always present.
  function productKey(product) {
    return product.product_url || product.id;
  }

  function toggleSelect(product) {
    const key = productKey(product);
    if (!key) return;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const selectedProducts = products.filter((p) => selectedIds.has(productKey(p)));
  // Default: no selection means "all products" (as specified).
  const targetProducts = selectedProducts.length ? selectedProducts : products;

  async function handleFindManufacturers() {
    if (!targetProducts.length) return;
    const controller = newAbort();
    setMfrLoading(true);
    setMfrWarnings([]);
    setMfrGroups(null); // clear previous results so only the progress bar shows
    setCheckedSuppliers(new Set());
    // Jump straight down to the manufacturer section (progress bar) on click.
    requestAnimationFrame(() =>
      mfrAnchorRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
    );
    try {
      const data = await searchManufacturers(targetProducts, { mfrSites, signal: controller.signal });
      setMfrGroups(data.groups);
      setMfrWarnings(data.warnings || []);
    } catch (err) {
      if (isAbortError(err)) return;
      setMfrWarnings([err.message || "Manufacturer search failed"]);
      setMfrGroups([]);
    } finally {
      setMfrLoading(false);
    }
  }

  const mfrButtonLabel = selectedProducts.length
    ? t("findMfrSelected", selectedProducts.length)
    : t("findMfrAll", products.length);

  const supplierKey = (productId, i) => `${productId}-${i}`;

  // Flat list of every listed supplier with a stable selection key + product ref.
  const allSuppliers = (mfrGroups || []).flatMap((g) =>
    g.suppliers.map((s, i) => ({ ...s, _key: supplierKey(productKey(g.product), i), _product: g.product.title }))
  );
  const checkedList = allSuppliers.filter((s) => checkedSuppliers.has(s._key));

  function toggleSupplier(key) {
    setCheckedSuppliers((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  // Header checkbox per product group: select/clear all of its suppliers.
  function toggleGroup(group) {
    const keys = group.suppliers.map((_, i) => supplierKey(productKey(group.product), i));
    const allChecked = keys.every((k) => checkedSuppliers.has(k));
    setCheckedSuppliers((prev) => {
      const next = new Set(prev);
      keys.forEach((k) => (allChecked ? next.delete(k) : next.add(k)));
      return next;
    });
  }
  const groupAllChecked = (group) =>
    group.suppliers.length > 0 &&
    group.suppliers.every((_, i) => checkedSuppliers.has(supplierKey(productKey(group.product), i)));

  // Workbench derivations.
  const shownProducts = useMemo(() => applyResultFilters(products, filters), [products, filters]);

  return (
    <div className="page">
      <h1 className="page-heading">{t("bestHeading")}</h1>

      <form onSubmit={handleSubmit}>
        <label className="field-label" htmlFor="product-query">
          {t("bestWhat")}
        </label>
        {/* One search bar: type a keyword, or tap the camera to search by photo
            (reverse-image via Google Lens). */}
        <div className="search-input-wrap">
          <input
            id="product-query"
            className="text-input"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("queryPlaceholder")}
            disabled={loading}
          />
          <button
            type="button"
            className="search-camera"
            onClick={() => fileInputRef.current?.click()}
            disabled={loading}
            title={t("searchByPhoto")}
            aria-label={t("searchByPhoto")}
          >
            <CameraIcon />
          </button>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp,image/bmp,image/avif"
            ref={fileInputRef}
            onChange={handleFileChosen}
            style={{ display: "none" }}
          />
        </div>

        {rawPhoto && (
          <div className="lens-cropper">
            <span className="field-label">{t("lensCropHint")}</span>
            <ImageCropper file={rawPhoto} onConfirm={handleCropConfirm} onCancel={() => setRawPhoto(null)} busy={loading} />
          </div>
        )}

        {imagePreview && !loading && !rawPhoto && (
          <div className="lens-reference">
            <img src={imagePreview} alt={t("searchByPhoto")} />
            <span className="lens-reference-label">{t("lensReference")}</span>
          </div>
        )}

        <span className="field-label">{t("sources")}</span>
        <SiteFilter selected={sites} onChange={setSites} disabled={loading} sites={BESTSELLER_SITES} />

        <button type="submit" className="primary-button" disabled={loading || !query.trim()}>
          {t("bestFind")}
        </button>
        <SavedSearches currentQuery={query.trim()} currentSites={sites} onRun={runNamedSearch} />
      </form>

      {loading && (
        <ProgressBar
          label={searchMode === "lens" ? t("bestSearchingLens") : t("bestSearching")}
          durationMs={searchMode === "lens" ? 120000 : PRODUCT_SEARCH_MS}
        />
      )}

      {error && <div className="status-message error">{error}</div>}

      {!loading && !error && warnings.length > 0 && (
        <div className="status-message warning">
          {warnings.map((w, i) => (
            <div key={i}>{w}</div>
          ))}
        </div>
      )}

      {!loading && !error && hasSearched && products.length === 0 && (
        <div className="status-message">{t("noResults")}</div>
      )}

      {!loading && !error && lensMode === "exact" && (
        <LensBanner mode="exact" count={products.length} />
      )}

      {products.length > 0 && (
        <>
          <MarketSnapshot products={shownProducts} />

          <div className="results-controls">
            <ResultsToolbar
              filters={filters}
              onChange={setFilters}
              shownCount={shownProducts.length}
              totalCount={products.length}
            />
            <button
              type="button"
              className="secondary-button results-export"
              onClick={() => exportProductsToExcel(shownProducts, "products.xlsx")}
              disabled={!shownProducts.length}
            >
              {t("exportExcel")}
            </button>
          </div>

          <div className="mfr-actionbar">
            <div className="mfr-actionbar-left">
              <span className="mfr-hint">{t("selectHint")}</span>
              <div className="mfr-sources">
                <span className="mfr-sources-label">{t("mfrSources")}</span>
                <SiteFilter selected={mfrSites} onChange={setMfrSites} disabled={mfrLoading} sites={MANUFACTURER_SITES} />
              </div>
            </div>
            <button
              type="button"
              className="primary-button mfr-button"
              onClick={handleFindManufacturers}
              disabled={mfrLoading}
            >
              {mfrLoading ? t("findMfrSearching") : mfrButtonLabel}
            </button>
          </div>

          {shownProducts.length === 0 ? (
            <div className="status-message">{t("wbNoMatches")}</div>
          ) : (
            <div className="results-grid products-grid">
              {shownProducts.map((product) => (
                <ProductCard
                  key={productKey(product)}
                  product={product}
                  selectable
                  selected={selectedIds.has(productKey(product))}
                  onToggleSelect={toggleSelect}
                />
              ))}
            </div>
          )}
        </>
      )}

      <div ref={mfrAnchorRef} />
      {mfrLoading && <ProgressBar label={t("findMfrSearching")} durationMs={MFR_SEARCH_MS} />}

      {mfrWarnings.length > 0 && (
        <div className="status-message warning">
          {mfrWarnings.map((w, i) => (
            <div key={i}>{w}</div>
          ))}
        </div>
      )}

      {mfrGroups && mfrGroups.length > 0 && (
        <div className="mfr-results">
          <div className="mfr-results-head">
            <h2 className="section-heading">{t("mfrResultsHeading")}</h2>
            <div className="mfr-head-actions">
              <button
                type="button"
                className="primary-button mfr-msg-button"
                onClick={() => setMessageOpen(true)}
                disabled={checkedList.length === 0}
              >
                <MailIcon />
                {checkedList.length > 0 ? t("sendMessageCount", checkedList.length) : t("sendMessage")}
              </button>
            <button
              type="button"
              className="secondary-button"
              onClick={() =>
                exportProductsToExcel(
                  // Each row = one supplier offer, carrying the real PRODUCT
                  // image + name so the export includes pictures.
                  mfrGroups.flatMap((g) =>
                    g.suppliers.map((s) => ({
                      ...s,
                      image_url: g.product.image_url,
                      title: g.product.title,
                      product_url: g.product.product_url,
                    }))
                  ),
                  "manufacturers.xlsx"
                )
              }
            >
              {t("exportExcel")}
            </button>
            </div>
          </div>

          {mfrGroups.map((group) => (
            <div key={productKey(group.product)} className="mfr-group">
              {/* Actual product photo + info at the top of the group */}
              <div className="mfr-product-head">
                <a href={group.product.product_url} target="_blank" rel="noreferrer" className="mfr-product-thumb">
                  {group.product.image_url ? (
                    <img src={group.product.image_url} alt={group.product.title} />
                  ) : (
                    <div className="product-image-placeholder">{t("noImage")}</div>
                  )}
                </a>
                <div className="mfr-product-info">
                  <span className="site-badge site-badge-inline" style={{ background: SITE_COLORS[group.product.site] || "#888" }}>
                    {SITE_LABELS[group.product.site] || group.product.site}
                  </span>
                  <a href={group.product.product_url} target="_blank" rel="noreferrer" className="mfr-product-name">
                    {group.product.title}
                  </a>
                  {group.product.price_text && <div className="mfr-product-price">{group.product.price_text}</div>}
                  <div className="mfr-count">{t("manufacturersCount", group.suppliers.length)}</div>
                </div>
              </div>

              {/* Manufacturers — text rows only, no images */}
              <div className="mfr-table">
                <div className="mfr-row mfr-row-head">
                  <span className="mfr-check-cell">
                    <input
                      type="checkbox"
                      className="mfr-checkbox"
                      checked={groupAllChecked(group)}
                      onChange={() => toggleGroup(group)}
                      aria-label={t("selectAllSuppliers")}
                    />
                  </span>
                  <span>{t("colSource")}</span>
                  <span>{t("colMatch")}</span>
                  <span>{t("colCompany")}</span>
                  <span>{t("colPhone")}</span>
                  <span>{t("colRating")}</span>
                  <span>{t("colPrice")}</span>
                  <span>{t("colMoq")}</span>
                </div>
                {group.suppliers.map((s, i) => {
                  const key = `${productKey(group.product)}-${i}`;
                  const checked = checkedSuppliers.has(key);
                  return (
                  <div className={`mfr-row ${checked ? "mfr-row-checked" : ""}`} key={key}>
                    <span className="mfr-check-cell">
                      <input
                        type="checkbox"
                        className="mfr-checkbox"
                        checked={checked}
                        onChange={() => toggleSupplier(key)}
                        aria-label={t("selectSupplier", s.seller_name)}
                      />
                    </span>
                    <span>
                      <span className="site-badge site-badge-inline" style={{ background: SITE_COLORS[s.site] || "#888" }}>
                        {SITE_LABELS[s.site] || s.site}
                      </span>
                    </span>
                    <MatchBadge supplier={s} t={t} />
                    <span className="mfr-company">
                      <span className="mfr-company-name">
                        {s.seller_name}
                      </span>
                      {s.email && (
                        <a className="mfr-email" href={`mailto:${s.email}`}>
                          <MailIcon />
                          {s.email}
                        </a>
                      )}
                      {/* Business type, years and the named contact are what
                          these marketplaces actually publish — show them
                          rather than leaving the cell to a name alone. */}
                      {(s.contact_name || s.business_type || s.years_active) && (
                        <span className="mfr-company-meta">
                          {[
                            s.contact_name,
                            s.business_type,
                            s.years_active ? t("yearsOnPlatform", s.years_active) : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </span>
                      )}
                    </span>
                    {s.phone ? (
                      <a className="mfr-phone" href={`tel:${s.phone}`}>{s.phone}</a>
                    ) : (
                      <span
                        className="mfr-phone mfr-phone-na"
                        title={s.pages_scanned ? t("noContactScanned", s.pages_scanned) : undefined}
                      >
                        {s.pages_scanned ? t("nonePublished") : t("colNotAvailable")}
                      </span>
                    )}
                    <StarRating value={s.rating} />
                    <span className="mfr-price">{s.price_text}</span>
                    <span className="mfr-moq">{s.moq}</span>
                  </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {messageOpen && (
        <MessageModal
          recipients={checkedList}
          onClose={() => setMessageOpen(false)}
          onSent={() => setCheckedSuppliers(new Set())}
          t={t}
        />
      )}
    </div>
  );
}
