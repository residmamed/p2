import { useEffect, useMemo, useRef, useState } from "react";
import SiteFilter from "./SiteFilter";
import ProductCard from "./ProductCard";
import ProgressBar from "./ProgressBar";
import SearchTicker from "./SearchTicker";
import PrefetchRing from "./PrefetchRing";
import ImageCropper from "./ImageCropper";
import MarketSnapshot from "./MarketSnapshot";
import ResultsToolbar, { applyResultFilters, DEFAULT_FILTERS } from "./ResultsToolbar";
import SavedSearches from "./SavedSearches";
import LensBanner from "./LensBanner";
import { exportProductsToExcel } from "../exportExcel";
import { searchBestSellers, searchBestSellersByImage, findSuppliersByImage, findMoreFromStore, clearSupplierCache, PRODUCT_SEARCH_MS, LENS_SOURCING_MS, MFR_SEARCH_MS } from "../api";
import { PRODUCT_SEARCH_SITES, MANUFACTURER_SITES, SITE_LABELS, SITE_COLORS } from "../sites";
import { useI18n } from "../i18n";
import { useRecentSearches, RUN_SEARCH_EVENT } from "../store";
import { userWarnings } from "../warnings";
import { splitSuppliers } from "../supplierFilter";

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

  // Lens Sourcing rows get their own wording. Nothing in that pipeline compares
  // the two products — Google Lens matched an image, which is a claim about
  // pictures, not about products — so these must never render as a verified
  // match. An exact hit means Lens found the identical image file on that page;
  // a visual one means it merely looks like it.
  if (supplier.match_basis === "lens") {
    const exact = tier === "lens_exact";
    return (
      <span
        className={`mfr-match mfr-match-${exact ? "exact" : "similar"}`}
        title={t(exact ? "matchByLensExact" : "matchByLensVisual")}
      >
        <span className="mfr-match-tier">{t(exact ? "matchTier_lens_exact" : "matchTier_lens_visual")}</span>
      </span>
    );
  }

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

// Marks the listing title as leaving the app for the marketplace.
function ExternalIcon() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
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
    // WhatsApp and SMS both go to the supplier's phone number, which this UI
    // does not display. The recipient chip names the channel instead, so the
    // user still knows how the message will be sent without the number being
    // put on screen.
    if (m === "whatsapp") return { kind: "whatsapp", value: null };
    if (m === "sms") return { kind: "sms", value: null };
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
// The accounts outreach will eventually send through: the user's own mailbox,
// and their logins on the marketplaces whose enquiry forms are the only way to
// reach most of these suppliers. `connected` is hard-coded false because no
// account can be connected yet — when the OAuth flows land, this is the single
// place that changes.
const CONNECTABLE_ACCOUNTS = [
  { id: "gmail", label: "Gmail", connected: false },
  { id: "alibaba", label: "Alibaba", connected: false },
  { id: "1688", label: "1688", connected: false },
];

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

            {/* Account connection status. Nothing is wired up yet — there is
                no OAuth flow behind any of these — so every dot is red and
                none of them is a button. A "Connect" control that did nothing
                would be worse than an honest status light. */}
            <div className="msg-accounts">
              <span className="field-label">{t("msgConnectAccounts")}</span>
              <ul className="msg-account-list">
                {CONNECTABLE_ACCOUNTS.map((a) => (
                  <li key={a.id} className="msg-account">
                    <span
                      className={`msg-account-dot ${a.connected ? "connected" : "disconnected"}`}
                      aria-hidden="true"
                    />
                    <span className="msg-account-name">{a.label}</span>
                    <span className="msg-account-state">
                      {t(a.connected ? "msgAccountConnected" : "msgAccountDisconnected")}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="msg-account-note">{t("msgConnectSoon")}</p>
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
                  {/* The listing, not the company — the same thing the grid
                      rows show, so a recipient chip can be matched back to the
                      row it came from. */}
                  <span className="msg-chip-name">{r.title || SITE_LABELS[r.site] || r.site}</span>
                  <span className={`msg-chip-ch msg-chip-ch-${ch.kind}`}>
                    {ch.kind === "none" ? <StoreIcon /> : CHANNEL_ICON[ch.kind]}
                    {ch.kind === "platform"
                      ? t("msgViaSite", ch.value)
                      : ch.kind === "none"
                      ? t("msgUnreachable")
                      // Falls back to the channel's own name where there is no
                      // displayable address — i.e. WhatsApp and SMS.
                      : ch.value || t(`method${ch.kind[0].toUpperCase()}${ch.kind.slice(1)}`)}
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

// How the two prefetch passes divide the corner ring. Both passes now cover
// every product, but the fast Lens one takes seconds where the deep marketplace
// one takes minutes. Splitting the ring evenly would race it to near-full in the
// first few seconds and then leave it apparently stuck for the rest of the run,
// which is the opposite of what it's for.
const FAST_SHARE = 0.25;

// How many products the background deep pass searches at once.
//
// It used to be one, on the belief that a deep search holds three cloud browsers
// for its three sites. On the by-URL path this endpoint actually takes, Alibaba
// and 1688 go to Apify actors and open no browser at all — only Made-in-China
// drives one. So the normal cost is ONE Browserbase session per product, and
// three only in the worst case, when both Apify actors come back empty and fall
// back to the browser upload.
//
// Sized against a Browserbase Developer plan (25 concurrent). Six products is
// six sessions normally and eighteen at the worst, leaving headroom under 25 —
// and going over does not queue, the session create fails outright, which is
// why this is deliberately short of the cap rather than at it. On a Free plan
// (3 concurrent) this must be 1.
const PREFETCH_PARALLEL = 6;

// How many times the background run comes back to a product whose search threw
// rather than answering, and how long it waits before each retry.
//
// The failures worth retrying are the transient ones — a captcha'd browser
// session, a Browserbase session refused because the plan's three were busy, a
// timeout — and those clear on their own given a moment, which is what the wait
// is for. Bounded because some failures are permanent (a photo URL the supplier
// sites won't accept), and an unbounded retry on one of those is an infinite
// loop that bills for every turn of it.
const PREFETCH_ROUNDS = 4;
const PREFETCH_RETRY_MS = 15000;

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
  const [stopped, setStopped] = useState(false);
  const [products, setProducts] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [lensMode, setLensMode] = useState(null); // null | "exact" | "similar"
  const [searchMode, setSearchMode] = useState("text"); // drives the loading label
  const [selectedIds, setSelectedIds] = useState(() => new Set());

  // Manufacturer search state
  const [mfrLoading, setMfrLoading] = useState(false);
  const [mfrGroups, setMfrGroups] = useState(null); // null = not searched yet
  const [mfrWarnings, setMfrWarnings] = useState([]);
  const [mfrLatency, setMfrLatency] = useState(null);
  // How many products fell through to the slow marketplace search, so the
  // progress bar can say so instead of sitting at 100%.
  const [deepSearching, setDeepSearching] = useState(0);
  // Real supplier-search progress: { phase, done, total, found }. Each product
  // is its own request and they land separately, so this is a count and not an
  // estimate — including `found`, which is the question the wait is about.
  const [mfrProgress, setMfrProgress] = useState(null);
  // The background supplier prefetch, for the corner ring: { pct, found, ready }.
  // Null until a product search finishes and the prefetch starts.
  const [prefetch, setPrefetch] = useState(null);
  const [prefetchHidden, setPrefetchHidden] = useState(false);
  // Per product, how many supplier listings have been found for it so far:
  // { [productKey]: { lens, deep } }. Fed by the background prefetch and by the
  // supplier search alike, which is why the two passes are recorded separately
  // and by max — both report the same product more than once (the second run
  // answers from cache), and adding those up would inflate the count.
  const [supplierHits, setSupplierHits] = useState({});
  const [mfrSites, setMfrSites] = useState(MANUFACTURER_SITES.map((s) => s.id));
  // Per-store "find more" status, keyed by site id:
  // { loading?, exhausted?, added?, error? }. Reset on every new search.
  const [moreState, setMoreState] = useState({});

  // Suppliers checked for messaging (keyed `${productId}-${index}`) + modal.
  const [checkedSuppliers, setCheckedSuppliers] = useState(() => new Set());
  const [messageOpen, setMessageOpen] = useState(false);

  // Workbench state: refine/sort filters.
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const { push: pushRecent } = useRecentSearches();

  const abortRef = useRef(null);
  // The silent supplier prefetch runs on its own controller, so that starting a
  // new product search cancels the previous prefetch — its results are for
  // products no longer on screen, and the Lens quota shouldn't pay for them —
  // without touching the foreground search's controller.
  const prefetchAbortRef = useRef(null);
  const mfrAnchorRef = useRef(null);
  const fileInputRef = useRef(null);

  function newAbort() {
    abortRef.current?.abort();
    const c = new AbortController();
    abortRef.current = c;
    return c;
  }

  // A store search runs five sites at once and can take a couple of minutes;
  // before this the only way out was reloading the page and losing the results
  // already on screen. Aborting the fetch is enough — every runner clears its
  // own loading flag in `finally` — but both flags are cleared here too so the
  // button is honest even if the request has already resolved.
  function handleStop() {
    abortRef.current?.abort();
    setLoading(false);
    setMfrLoading(false);
    // Remembered so the empty grid can say "you stopped this" — otherwise the
    // run ends on "No results found", which blames the stores for a search the
    // user cancelled.
    setStopped(true);
  }

  // No store selected means no search: the backend reads an absent site list as
  // "all five", so an empty picker used to quietly search everything — the one
  // thing the user just said they didn't want.
  const noStores = sites.length === 0;

  // Shared product-search runner for both the text and photo entry points.
  // `mode` is "text" or "lens"; a lens search reports back exact/similar via
  // data.lensMode so the UI can explain what Google Lens found.
  async function runProductSearch(searchFn, mode = "text") {
    const controller = newAbort();
    setSearchMode(mode);
    setLoading(true);
    setError(null);
    setHasSearched(true);
    setStopped(false);
    setSelectedIds(new Set());
    setMfrGroups(null);
    setMfrWarnings([]);
    setLensMode(null);
    setMoreState({}); // a store that was exhausted for the last query isn't for this one
    // The previous run's prefetch is about to be aborted and its results
    // dropped, so its ring must not carry a "ready" over into this search.
    setPrefetch(null);
    setPrefetchHidden(false);
    setSupplierHits({});
    setFilters(DEFAULT_FILTERS); // stale refinements don't apply to a new set
    // The previous query's prefetched suppliers are for products that are about
    // to leave the screen. Dropped rather than carried, so the cache only ever
    // holds answers for what's actually in the grid.
    clearSupplierCache();
    let found = null;
    try {
      const data = await searchFn(controller.signal);
      setProducts(data.results);
      setWarnings(data.warnings || []);
      if (mode === "lens") setLensMode(data.lensMode || "similar");
      found = data.results;
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err.message || "Something went wrong");
      setProducts([]);
    } finally {
      setLoading(false);
    }

    // Start looking for suppliers now — both passes, fast and deep — silently,
    // so that pressing the button later is fast. Nothing about the page changes
    // as a result of this — see prefetchSuppliers. `found` is passed explicitly
    // because `products` state hasn't committed yet in this closure.
    //
    // Not awaited: the product grid is already on screen and this run is
    // supposed to be invisible. An aborted search returns above, so a cancelled
    // or failed run never triggers it.
    if (found?.length) prefetchSuppliers(found).catch(() => {});
  }

  function handleSubmit(e) {
    e.preventDefault();
    if (!query.trim()) return;
    if (noStores) {
      setError(t("pickStoreFirst"));
      return;
    }
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
    // A chip saved before stores were required carries none. Restore the query
    // and say what's missing rather than searching all five on its behalf.
    if (!nextSites.length) {
      setError(t("pickStoreFirst"));
      return;
    }
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
    // The photo path filters Lens hits down to the chosen stores, so it needs
    // one just as much as the keyword path does.
    if (noStores) {
      setRawPhoto(null);
      setError(t("pickStoreFirst"));
      return;
    }
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

  // Record a product's FINAL supplier count — only ever called from
  // onProductDone, never from a pass still in flight, so the mark on a card
  // means "we finished looking for this item" and the number behind it is the
  // whole list rather than however much of it had arrived.
  //
  // Each report replaces the last rather than accumulating: the same product is
  // reported by the prefetch and again by the search the user runs, and a later
  // report is simply the better answer — including when it is zero, which is
  // what a re-run against a narrower set of marketplaces should produce.
  function noteSupplierHit(product, count) {
    const key = product && productKey(product);
    if (!key) return;
    setSupplierHits((prev) => {
      if ((prev[key] ?? 0) === count) return prev;
      const next = { ...prev };
      if (count > 0) next[key] = count;
      else delete next[key];
      return next;
    });
  }
  const supplierHitCount = (product) => supplierHits[productKey(product)] ?? 0;

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

  // Warm the supplier cache for the products just found, and render nothing.
  //
  // This is a prefetch, not a search: no progress bar, no results, no warnings.
  // Suppliers appear only when the user asks for them. The point is purely that
  // the asking is then fast — api.js caches each lookup by product photo, so the
  // click below either finds the answer already there or joins a request already
  // in flight, instead of starting from nothing.
  //
  // Every product in the grid, through both passes. It used to cover only the
  // top twelve, and only the top six of those through the deep pass — which made
  // the button's whole purpose "finish the prefetch's work in the foreground",
  // and, worse, put the finished-looking mark on products whose deep search had
  // never been run. A press then went straight into the slow marketplace pass
  // for exactly the items the grid had already called done. The unasked-for run
  // costs a Lens lookup and a set of browser sessions per product, so this is
  // the expensive choice; it is also the only one where the mark means what it
  // says and the button has nothing left to do.
  async function prefetchSuppliers(list) {
    if (!list.length || !mfrSites.length) return;
    prefetchAbortRef.current?.abort();
    const controller = new AbortController();
    prefetchAbortRef.current = controller;
    const targets = list;

    // One fraction across both passes, for the corner ring. The prefetch stays
    // silent in every other respect — no results, no warnings, nothing moves on
    // the page — but "has the wait already been paid?" is worth answering
    // without making the user press the button to find out.
    //
    // The running total is held per product and summed, rather than accumulated
    // as each report lands. A product can be searched more than once here — that
    // is what the retry rounds below are — and its second pass answers from
    // cache with the same rows the first one found, so anything that added as it
    // went would count those rows twice and report a supplier count that grew
    // with the failures rather than with the findings.
    const counts = new Map();
    const totalFound = () => {
      let n = 0;
      for (const c of counts.values()) n += c;
      return n;
    };
    const report = (fastDone, deepDone, ready = false) => {
      if (controller.signal.aborted) return;
      const fast = fastDone / targets.length;
      const deep = deepDone / targets.length;
      setPrefetch({
        pct: 100 * (FAST_SHARE * fast + (1 - FAST_SHARE) * deep),
        found: totalFound(),
        ready,
      });
    };
    report(0, 0);

    // Fast Lens pass first, all products at once — it is cheap and quick, and it
    // decides which products even need the slow pass.
    //
    // Nothing is done with the result: it lives in api.js's cache, which is the
    // entire purpose. Failures are swallowed for the same reason — a prefetch
    // that fails must be invisible, and the click will retry it.
    let fastDone = 0;
    await findSuppliersByImage(targets, {
      mfrSites,
      signal: controller.signal,
      deepFallback: false,
      onProgress: (p) => {
        fastDone = p.done;
        if (p.product) counts.set(productKey(p.product), p.count);
        report(fastDone, 0);
      },
      // Deliberately no onProductDone. This call runs no deep pass, so as far as
      // IT is concerned every product is finished — but the loop below is still
      // to come for all of them and can add to any. Marking a card here would
      // put the "suppliers found" dot on a product whose search is half done,
      // which is the one thing the dot must never mean.
    }).catch(() => {});
    if (controller.signal.aborted) return;

    // Then the deep marketplace search, which is the whole point of prefetching.
    // Lens has no Chinese B2B listing for most branded retail products, so a
    // Lens-only prefetch warmed the cheap half of the work and left the
    // expensive half — minutes of driven browsers — to begin on the click. That
    // wait is exactly what "searching the marketplaces directly (slower)" was
    // reporting. Starting it as soon as the products land means it is normally
    // finished, or well under way, before anyone presses anything.
    //
    // One product per call, PREFETCH_PARALLEL of them in flight — a call per
    // product because a product Lens already answered then costs nothing here
    // (its fast result is cached, this call finds suppliers for it, and no deep
    // search is triggered), and a bounded number in flight because the ceiling
    // is the Browserbase plan's concurrent-browser cap.
    //
    // The run does not end at the bottom of the list — it ends when every
    // product has an answer. A search that threw is not one: a captcha'd session
    // or a refused browser tells us nothing about whether the product has
    // suppliers, and a product left in that state sits in the grid looking
    // exactly like one that was searched properly and came back empty. So the
    // products that failed are gathered up and gone round again.
    //
    // "Searched, found nothing" is not a failure and is never retried. It is a
    // real answer, the request would be identical, and the only thing a second
    // attempt would reliably produce is a second bill.
    let deepDone = 0;
    let pending = targets;
    for (let round = 0; pending.length && round < PREFETCH_ROUNDS; round += 1) {
      if (round) {
        // A beat before trying again. What makes these searches fail is mostly
        // contention — Browserbase allows three concurrent sessions and one deep
        // search uses all three — so retrying the instant it failed mostly buys
        // the same failure.
        await new Promise((resolve) => setTimeout(resolve, PREFETCH_RETRY_MS));
        if (controller.signal.aborted) return;
      }
      const outstanding = [];
      // A shared cursor rather than a chunked Promise.all: chunks move at the
      // pace of their slowest member and leave the rest of the workers idle at
      // every boundary, and these searches differ by minutes. Each worker takes
      // the next product the moment it is free, so the plan's browsers stay
      // busy until there is genuinely nothing left to search.
      //
      // `cursor++` needs no lock — JS runs this synchronously between awaits, so
      // two workers cannot read the same index.
      let cursor = 0;
      const worker = async () => {
        while (cursor < pending.length) {
          // A new product search aborts the run here. It cannot cancel a deep
          // search already in flight — that request is shared through the
          // cache, so it carries no caller's signal — which bounds the waste to
          // the products being searched when the user moved on.
          if (controller.signal.aborted) return;
          const product = pending[cursor++];
          const data = await findSuppliersByImage([product], {
            mfrSites,
            signal: controller.signal,
            deepFallback: true,
            // This product's last pass — mark its card now, with the whole list.
            onProductDone: (prod, count) => {
              counts.set(productKey(prod), count);
              noteSupplierHit(prod, count);
              report(fastDone, deepDone);
            },
          }).catch(() => null);
          if (controller.signal.aborted) return;
          // Nothing usable came back, so the product is not done and the ring
          // must not count it. It goes into the next round instead.
          if (!data || data.unresolved.length) {
            outstanding.push(product);
            continue;
          }
          deepDone += 1;
          report(fastDone, deepDone);
        }
      };
      await Promise.all(
        Array.from({ length: Math.min(PREFETCH_PARALLEL, pending.length) }, worker)
      );
      if (controller.signal.aborted) return;
      pending = outstanding;
    }
    // `deepDone` and not targets.length: if a product defeated every round, the
    // ring stops short of full and says so. Claiming 100% here would put the
    // finished mark on a run that never finished — the exact thing the retries
    // exist to prevent.
    report(fastDone, deepDone, true);
  }

  async function handleFindManufacturers() {
    if (!targetProducts.length) return;
    // Same rule as the store picker: an empty source list reads as "all of
    // them" at the API, which is not what an empty picker looks like.
    if (!mfrSites.length) {
      setError(t("pickSourceFirst"));
      return;
    }
    const controller = newAbort();
    setMfrLoading(true);
    setStopped(false);
    setError(null);
    setMfrWarnings([]);
    setMfrLatency(null);
    setDeepSearching(0);
    setMfrProgress(null);
    setMfrGroups(null); // clear previous results so only the progress bar shows
    setCheckedSuppliers(new Set());
    // Jump straight down to the manufacturer section (progress bar) on click.
    requestAnimationFrame(() =>
      mfrAnchorRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
    );
    try {
      // Lens Sourcing: SerpApi Google Lens for the match, Oxylabs for the
      // supplier behind it. Seconds instead of the minutes the browser-upload
      // pipeline took, and it can't be captcha'd because no browser is driven.
      // /api/sourcing/by-url is still there and still finds listings this
      // can't — Lens only knows what it has indexed.
      //
      // Whatever the prefetch already fetched comes back from cache here, which
      // is the only visible effect the prefetch has.
      const data = await findSuppliersByImage(targetProducts, {
        mfrSites,
        signal: controller.signal,
        // Lens only knows what the web hosts; for branded retail products it
        // often has no Chinese B2B listing at all. Those products fall through
        // to the marketplaces' own image indexes rather than showing nothing.
        onDeepSearch: (n) => setDeepSearching(n),
        // Fires as each product's search lands. A cancelled run is left alone —
        // cached lookups aren't tied to this controller and resolve after Stop,
        // which would otherwise tick a bar that is no longer on screen.
        onProgress: (p) => {
          if (!controller.signal.aborted) setMfrProgress(p);
        },
        onProductDone: (product, count) => {
          if (!controller.signal.aborted) noteSupplierHit(product, count);
        },
      });
      // Cached lookups are shared and so aren't tied to this controller — they
      // resolve even after Stop. Checked explicitly, or a cancelled search would
      // still fill the grid.
      if (controller.signal.aborted) return;
      setMfrGroups(data.groups);
      setMfrWarnings(data.warnings || []);
      setMfrLatency(data.latencyMs ?? null);
    } catch (err) {
      if (isAbortError(err)) return;
      setError(err.message || t("mfrSearchFailed"));
      setMfrGroups([]);
    } finally {
      setMfrLoading(false);
    }
  }

  const mfrButtonLabel = selectedProducts.length
    ? t("findMfrSelected", selectedProducts.length)
    : t("findMfrAll", products.length);

  // What the supplier progress bar shows. The two passes count different things
  // — the fast one counts the basket, the slow one counts only what fell
  // through to it — so a report is used only while its own pass is the one being
  // labelled. In the gap between the label switching and the first slow product
  // landing there is nothing real to show yet, and the bar falls back to its
  // time estimate rather than pairing one pass's counts with the other's words.
  const mfrPhase = deepSearching ? "deep" : "lens";
  const mfrLive = mfrProgress?.phase === mfrPhase ? mfrProgress : null;
  const mfrPct = mfrLive?.total ? (mfrLive.done / mfrLive.total) * 100 : null;
  const mfrDetail = !mfrLive
    ? null
    : mfrPhase === "deep"
    ? t("supplierProgressDeep", mfrLive.done, mfrLive.total, mfrLive.found)
    : mfrLive.found
    ? t("supplierProgress", mfrLive.done, mfrLive.total, mfrLive.found)
    // Zero is a real answer and worth saying plainly — "0 suppliers found" next
    // to a moving bar reads as a bar that hasn't got there yet.
    : t("supplierProgressNone", mfrLive.done, mfrLive.total);

  const supplierKey = (productId, i) => `${productId}-${i}`;

  // What the grid actually shows: per product, only the listings something
  // vouched for as selling this product. `splitSuppliers` falls back to the
  // unfiltered list when nothing could be confirmed, so this can narrow a group
  // but never empty one.
  const mfrView = useMemo(() => {
    if (!mfrGroups) return null;
    return mfrGroups.map((group) => {
      const { sellers, unconfirmed, confirmedOnly } = splitSuppliers(group.suppliers);
      return {
        ...group,
        suppliers: sellers,
        hiddenUnconfirmed: unconfirmed.length,
        confirmedOnly,
      };
    });
  }, [mfrGroups]);

  // Every supplier found for a product, all of them, always. There was a cap of
  // five behind a "More" button; a sourcing list that hides rows by default
  // hides the cheapest one just as easily as the fifteenth.
  const visibleSuppliers = (group) => group.suppliers;

  // Flat list of every listed supplier with a stable selection key + product ref.
  const allSuppliers = (mfrView || []).flatMap((g) =>
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

  // Header checkbox per product group: select/clear the suppliers on screen.
  // Deliberately the visible rows only — a "select all" that silently checked
  // fifteen collapsed suppliers would send fifteen enquiries from a screen
  // showing five.
  function toggleGroup(group) {
    const keys = visibleSuppliers(group).map((_, i) => supplierKey(productKey(group.product), i));
    const allChecked = keys.every((k) => checkedSuppliers.has(k));
    setCheckedSuppliers((prev) => {
      const next = new Set(prev);
      keys.forEach((k) => (allChecked ? next.delete(k) : next.add(k)));
      return next;
    });
  }
  const groupAllChecked = (group) => {
    const visible = visibleSuppliers(group);
    return (
      visible.length > 0 &&
      visible.every((_, i) => checkedSuppliers.has(supplierKey(productKey(group.product), i)))
    );
  };

  // Workbench derivations.
  const shownProducts = useMemo(() => applyResultFilters(products, filters), [products, filters]);

  // "Find more", per store. Counted off `products` rather than a page number so
  // the count stays right no matter what else has changed the grid — a store
  // that returned nothing has no button, and one the user re-searched starts
  // over. `products` is the full set; the refine filters only hide rows, and
  // asking a store to skip rows it never sent would skip real results.
  const storeCounts = useMemo(() => {
    // A photo search has no keyword to page with — its results came from Google
    // Lens matching an image, and the stores were never queried by term. Paging
    // them would mean inventing a query the user never typed, so the row is
    // withheld rather than offering buttons that can only fail.
    if (searchMode !== "text" || !query.trim()) return [];
    const counts = new Map();
    for (const p of products) counts.set(p.site, (counts.get(p.site) || 0) + 1);
    // Ordered by the store picker, so the buttons don't reshuffle between runs.
    return sites.filter((s) => counts.has(s)).map((s) => ({ site: s, count: counts.get(s) }));
  }, [products, sites, searchMode, query]);

  async function handleFindMore(site) {
    const have = storeCounts.find((s) => s.site === site)?.count ?? 0;
    setMoreState((prev) => ({ ...prev, [site]: { loading: true } }));
    try {
      // Deliberately its own AbortController, not newAbort(): these run one
      // store at a time and must not cancel a supplier search — or each other —
      // the way starting a new product search does.
      const data = await findMoreFromStore(query.trim(), site, have);
      const fresh = data.results || [];
      if (fresh.length) {
        // Appended, not merged: the grid is grouped by store and the backend
        // already ranked these within their own store, so dropping them at the
        // end keeps each store's ordering intact.
        setProducts((prev) => {
          const seen = new Set(prev.map((p) => productKey(p)));
          return [...prev, ...fresh.filter((p) => !seen.has(productKey(p)))];
        });
      }
      setMoreState((prev) => ({
        ...prev,
        // An empty batch is a store saying it's finished, which is why this is
        // "exhausted" and not an error. The button stays visible and says so.
        [site]: { loading: false, exhausted: fresh.length === 0, added: fresh.length },
      }));
      setWarnings((prev) => [...prev, ...(data.warnings || [])]);
    } catch (err) {
      if (isAbortError(err)) return;
      setMoreState((prev) => ({
        ...prev,
        [site]: { loading: false, error: err.message || t("findMoreFailed") },
      }));
    }
  }

  // Scraper diagnostics ("retrying upload (1/3)", "no file input matched …")
  // are dropped and the sites they concerned summarised in one line. See
  // ../warnings.js — they still go to the server log, which is where a CSS
  // selector belongs.
  // Warnings are diagnostics, not product copy. They still explain a thin
  // result set when something is being debugged, so they go to the console
  // rather than to a yellow panel above the results — this screen is shown to
  // end users, and a wall of scraper commentary is not something to hand them.
  useEffect(() => {
    const shown = userWarnings(warnings, t);
    if (shown.length) console.debug("[product search]", ...shown);
  }, [warnings, t]);
  useEffect(() => {
    const shown = userWarnings(mfrWarnings, t);
    if (shown.length) console.debug("[supplier search]", ...shown);
  }, [mfrWarnings, t]);

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
            disabled={loading || noStores}
            title={noStores ? t("pickStoreFirst") : t("searchByPhoto")}
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
        <SiteFilter selected={sites} onChange={setSites} disabled={loading} sites={PRODUCT_SEARCH_SITES} />
        {noStores && <div className="field-hint">{t("pickStoreFirst")}</div>}

        <button
          type="submit"
          className="primary-button"
          disabled={loading || !query.trim() || noStores}
          title={noStores ? t("pickStoreFirst") : undefined}
        >
          {t("bestFind")}
        </button>
        <SavedSearches currentQuery={query.trim()} currentSites={sites} onRun={runNamedSearch} />
      </form>

      {loading && (
        <>
          <ProgressBar
            label={searchMode === "lens" ? t("bestSearchingLens") : t("bestSearching")}
            durationMs={searchMode === "lens" ? 120000 : PRODUCT_SEARCH_MS}
          />
          {/* The stores being searched, scrolling past. One request covers all
              of them, so there is no per-store progress to report — but naming
              them is honest about where the wait is going, and gives the half
              minute something to watch. */}
          <SearchTicker sites={sites} variant={searchMode === "lens" ? "lens" : "product"} />
          <div className="stop-row">
            <button type="button" className="secondary-button" onClick={handleStop}>
              {t("stopSearch")}
            </button>
          </div>
        </>
      )}

      {error && <div className="status-message error">{error}</div>}

      {!loading && !mfrLoading && stopped && (
        <div className="status-message">{t("searchStopped")}</div>
      )}

      {!loading && !error && !stopped && hasSearched && products.length === 0 && (
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
              disabled={mfrLoading || mfrSites.length === 0}
              title={mfrSites.length === 0 ? t("pickSourceFirst") : undefined}
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
                  supplierCount={supplierHitCount(product)}
                />
              ))}
            </div>
          )}

          {/* One button per store, because "more" means something different at
              each: Target can open another page of its best-selling sort, while
              IKEA returns its whole result set at once and is simply finished.
              A store that returned nothing for this query gets no button. */}
          <div className="find-more-row" hidden={storeCounts.length === 0}>
            <span className="find-more-label">{t("findMoreLabel")}</span>
            {storeCounts.map(({ site, count }) => {
              const state = moreState[site] || {};
              return (
                <span key={site} className="find-more-item">
                  <button
                    type="button"
                    className="secondary-button find-more-button"
                    onClick={() => handleFindMore(site)}
                    disabled={state.loading || state.exhausted}
                    style={{ borderColor: SITE_COLORS[site] }}
                  >
                    {state.loading
                      ? t("findMoreLoading", SITE_LABELS[site] || site)
                      : `${SITE_LABELS[site] || site} (${count})`}
                  </button>
                  {/* The store said it has nothing further — stated plainly,
                      since an inert button with no explanation reads as broken. */}
                  {state.exhausted && <span className="find-more-note">{t("findMoreNoMore")}</span>}
                  {state.error && (
                    <span className="find-more-note error">{state.error}</span>
                  )}
                </span>
              );
            })}
          </div>
        </>
      )}

      <div ref={mfrAnchorRef} />
      {mfrLoading && (
        <>
          <ProgressBar
            label={deepSearching ? t("deepSearching", deepSearching) : t("findMfrSearching")}
            durationMs={deepSearching ? MFR_SEARCH_MS : LENS_SOURCING_MS}
            value={mfrPct}
            detail={mfrDetail}
            key={deepSearching ? "deep" : "fast"}
          />
          <SearchTicker sites={mfrSites} variant="supplier" />
          <div className="stop-row">
            <button type="button" className="secondary-button" onClick={handleStop}>
              {t("stopSearch")}
            </button>
          </div>
        </>
      )}

      {mfrView && mfrView.length === 0 && !mfrLoading && (
        <div className="status-message">{t("mfrNoResults")}</div>
      )}

      {mfrView && mfrView.length > 0 && (
        <div className="mfr-results">
          <div className="mfr-results-head">
            <h2 className="section-heading">
              {t("mfrResultsHeading")}
              {mfrLatency != null && (
                <span className="mfr-latency">{t("mfrLatency", (mfrLatency / 1000).toFixed(1))}</span>
              )}
            </h2>
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
                  // Each row = one listing, carrying the retail product's photo
                  // so the export has pictures, but the listing's OWN title and
                  // URL: every row used to link back to the retail product it
                  // was found from, which is the one link the reader already has.
                  mfrView.flatMap((g) =>
                    g.suppliers.map((s) => ({
                      ...s,
                      image_url: g.product.image_url,
                      title: s.title || g.product.title,
                      product_url: s.product_url,
                      _sourceProduct: g.product.title,
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

          {mfrView.map((group) => (
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
                  {/* Say which of the two lists is on screen. A confirmed set
                      and a fallback set look identical otherwise, and the
                      fallback is the one to verify before contacting. */}
                  {group.confirmedOnly && group.hiddenUnconfirmed > 0 && (
                    <div className="mfr-count-note">
                      {t("hiddenUnconfirmed", group.hiddenUnconfirmed)}
                    </div>
                  )}
                  {!group.confirmedOnly && group.suppliers.length > 0 && (
                    <div className="mfr-count-note">{t("noneConfirmed")}</div>
                  )}
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
                  <span>{t("colListing")}</span>
                  <span>{t("colRating")}</span>
                  <span>{t("colPrice")}</span>
                  <span>{t("colMoq")}</span>
                </div>
                {visibleSuppliers(group).map((s, i) => {
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
                        aria-label={t("selectSupplier", s.title || SITE_LABELS[s.site] || s.site)}
                      />
                    </span>
                    <span>
                      <span className="site-badge site-badge-inline" style={{ background: SITE_COLORS[s.site] || "#888" }}>
                        {SITE_LABELS[s.site] || s.site}
                      </span>
                    </span>
                    <MatchBadge supplier={s} t={t} />
                    <span className="mfr-company">
                      {/* The listing itself, on the marketplace that hosts it —
                          the thing to open. Company names are deliberately not
                          shown: what this table is for is finding the product on
                          Alibaba / 1688 / Made-in-China, and the name behind a
                          listing is read by enrichment that often can't get one,
                          so it made half the rows look emptier than they are. */}
                      {s.product_url ? (
                        <a
                          className="mfr-listing"
                          href={s.product_url}
                          target="_blank"
                          rel="noreferrer"
                          title={t("openListingOn", SITE_LABELS[s.site] || s.site)}
                        >
                          <span className="mfr-listing-title">
                            {s.title || t("openListingOn", SITE_LABELS[s.site] || s.site)}
                          </span>
                          <ExternalIcon />
                        </a>
                      ) : (
                        // Nothing to open. Said plainly rather than rendered as
                        // a dead link — the URL is never guessed at.
                        <span className="mfr-listing-title">{s.title || t("listingNoLink")}</span>
                      )}
                      {s.email && (
                        <a className="mfr-email" href={`mailto:${s.email}`}>
                          <MailIcon />
                          {s.email}
                        </a>
                      )}
                    </span>
                    {/* No phone column. Supplier phone numbers are not
                        displayed anywhere in this UI — the email address and
                        the platform inbox are the contact routes offered. */}
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

      {/* Hidden the moment the supplier search itself runs: from then on the
          real progress bar and the results say everything this did, and two
          progress indicators for one job is one too many. */}
      {prefetch && !prefetchHidden && !mfrLoading && !mfrGroups && (
        <PrefetchRing
          pct={prefetch.pct}
          found={prefetch.found}
          ready={prefetch.ready}
          onDismiss={() => setPrefetchHidden(true)}
        />
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
