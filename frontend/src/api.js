const API_BASE = "http://127.0.0.1:8000";

async function handleResponse(response) {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Request failed (${response.status}): ${text}`);
  }
  return response.json();
}

export async function searchByText(query, { page = 1, sites = [], signal } = {}) {
  const params = new URLSearchParams({ q: query, page: String(page) });
  if (sites.length) params.set("sites", sites.join(","));
  const response = await fetch(`${API_BASE}/api/search/text?${params.toString()}`, { signal });
  return handleResponse(response);
}

import { siteForUrl } from "./sites";

// Progress-bar durations. These are estimates of how long the REAL searches
// take, measured against the live endpoints — not simulated delays. Product
// search fans out to Zyte (and a cloud browser for Temu/Costco); manufacturer
// search drives a browser upload per supplier site, so it's much slower.
export const PRODUCT_SEARCH_MS = 30000;
// The browser-upload pipeline's own pacing, kept for /api/sourcing/by-url.
export const MFR_SEARCH_MS = 120000;
// Lens Sourcing's. Products are searched concurrently, so the wall clock is the
// slowest single product rather than the sum. Measured on five cold products at
// once — the shape a real basket has, not the single image it is tempting to
// test with: 5.4s, 7.3s, 13.0s, 13.7s, 16.1s, so 16.1s wall clock. Warm, with
// the image already in the 30-day Lens cache, the same five took 8.7s.
//
// Sized for the cold case: a bar that finishes early and then sits at 100%
// reads as a hang, which is worse than one that runs slightly long.
export const LENS_SOURCING_MS = 20000;

// Product Search — real retail results for the selected sources, ranked by each
// site's best available demand signal (see backend bestsellers.py). Every
// product carries a `rank_basis` saying which signal produced its position.
export async function searchBestSellers(query, { sites = [], signal } = {}) {
  const params = new URLSearchParams({ q: query });
  if (sites.length) params.set("sites", sites.join(","));
  const response = await fetch(`${API_BASE}/api/bestsellers?${params.toString()}`, { signal });
  return handleResponse(response);
}

// One store's next batch, for its own "find more" button. `have` is how many
// rows from that store are already on screen, so the backend can skip them.
//
// An empty `results` is the normal way a store says it's finished — the caller
// shows "no more" rather than treating it as a failure. Warnings still travel,
// because "this store returns everything in one request" is worth knowing.
export async function findMoreFromStore(query, site, have, { signal } = {}) {
  const params = new URLSearchParams({ q: query, site, have: String(have) });
  const response = await fetch(`${API_BASE}/api/bestsellers/more?${params.toString()}`, { signal });
  return handleResponse(response);
}

const FALLBACK_LIMIT = 5;

// Turn a raw Google Lens response into the rows worth showing:
//   1. keep the EXACT-match hits (pixel-identical pages, site
//      "google_lens_exact") — re-tagged to the retail site their URL points at,
//      so the card shows the right badge — optionally narrowed to an allowlist
//      of sites;
//   2. if there are none, fall back to the 5 closest visual matches Lens
//      returned (site "google_lens", the shopping results), deduped by URL.
// `lensMode` ("exact" | "similar") tells the UI which happened.
//
// Shared by both entry points so a Lens search behaves identically wherever it
// is run: Product Search passes its selected retail sites as the allowlist,
// while Trending passes none — its site filter picks *supplier* sites, which a
// retail Lens hit never matches, so filtering on it would discard everything.
export function selectLensMatches(results, { sites = [] } = {}) {
  const allowed = sites.length ? new Set(sites) : null;
  const tagged = results.map((r) => ({
    ...r,
    _retail: siteForUrl(r.product_url),
    _exact: r.site === "google_lens_exact",
  }));
  const onSelectedSite = (r) => !allowed || (r._retail && allowed.has(r._retail));

  const dedupe = (rows) => {
    const seen = new Set();
    const out = [];
    for (const r of rows) {
      if (r.product_url && seen.has(r.product_url)) continue;
      if (r.product_url) seen.add(r.product_url);
      out.push(r);
    }
    return out;
  };
  const finalize = (r, exact, i) => {
    const { _retail, _exact, ...rest } = r;
    return {
      ...rest,
      site: _retail || rest.site,
      exact_match: exact,
      image_match: exact ? 1 : Math.max(0.6, 0.85 - i * 0.04),
    };
  };

  const exact = dedupe(tagged.filter((r) => r._exact && onSelectedSite(r)));
  if (exact.length) {
    return { results: exact.map((r) => finalize(r, true)), lensMode: "exact" };
  }

  // No exact match (on the selected sites) — show the closest visual matches.
  const closest = tagged.filter((r) => !r._exact);
  const pool = dedupe(closest.length ? closest : tagged).slice(0, FALLBACK_LIMIT);
  return {
    results: pool.map((r, i) => finalize(r, false, i)),
    lensMode: pool.length ? "similar" : null,
  };
}

// Picture search via Google Lens, narrowed to the user's selected retail sites.
export async function searchBestSellersByImage(file, { sites = [], signal } = {}) {
  const { results = [], warnings = [] } = await searchGoogleLens(file, { signal });
  return { ...selectLensMatches(results, { sites }), warnings };
}

// Manufacturer Search — for each chosen product, reverse-image-search its photo
// on the supplier sites and return the listings grouped per product.
//
// Runs one request per product rather than one batched call: each product needs
// its own browser session per supplier site, so batching would only hide the
// progress. Products resolve independently and a failure on one leaves the
// others intact — the same per-source degradation the backend already applies.
// The deep search, cached per product photo and site list — the same trick the
// fast path uses below, for the same reason, except here it saves minutes rather
// than seconds. The post-search prefetch now runs this pass too, so by the time
// the user presses the button the browser sessions have usually already been
// driven; without a cache the press would drive them all a second time.
//
// `settled` is tracked alongside the promise so callers can tell "already
// fetched" from "still fetching" — findSuppliersByImage uses it to decide
// whether to announce a slow search or stay quiet. The promise itself never
// exposes that.
const _deepCache = new Map();

function _lookupDeep(product, mfrSites) {
  const key = `${product.image_url}|${mfrSites.join(",")}`;
  const hit = _deepCache.get(key);
  if (hit) return hit;

  const params = new URLSearchParams({ image_url: product.image_url });
  if (mfrSites.length) params.set("sites", mfrSites.join(","));
  // No signal, deliberately: a cached lookup is shared, so one caller aborting
  // must not cancel a request another is waiting on. Callers check their own
  // signal after awaiting.
  const entry = {
    settled: false,
    promise: fetch(`${API_BASE}/api/sourcing/by-url?${params.toString()}`, { method: "POST" })
      .then(handleResponse)
      .then((data) => {
        entry.settled = true;
        return data;
      }),
  };
  _deepCache.set(key, entry);
  // Not cached on failure: a browser session that got captcha'd or timed out is
  // frequently fine on a retry, and the click is the retry.
  entry.promise.catch(() => _deepCache.delete(key));
  return entry;
}

// Whether the deep search for this product still has to be waited on — either
// never started, or started and not yet back.
function _deepPending(product, mfrSites) {
  const hit = _deepCache.get(`${product.image_url}|${mfrSites.join(",")}`);
  return !hit || !hit.settled;
}

// `signal` is accepted and ignored: every lookup here is cached and therefore
// shared, so no single caller may cancel it. Callers check their own signal after
// awaiting instead.
export async function searchManufacturers(products, { mfrSites = [] } = {}) {
  const targets = products.filter((p) => p.image_url);
  const skipped = products.length - targets.length;

  const settled = await Promise.all(
    targets.map(async (product) => {
      try {
        return { product, data: await _lookupDeep(product, mfrSites).promise };
      } catch (error) {
        if (error.name === "AbortError") throw error;
        return { product, error };
      }
    })
  );

  const groups = [];
  const warnings = [];
  if (skipped > 0) {
    warnings.push(`${skipped} product(s) had no photo to search with and were skipped.`);
  }

  for (const { product, data, error } of settled) {
    if (error) {
      warnings.push(`[${product.title?.slice(0, 40) ?? "product"}] ${error.message}`);
      continue;
    }
    warnings.push(...(data.warnings ?? []));
    // Flatten SourcingResult -> the supplier-row shape the UI renders, keeping
    // the match tier so a "similar" match is never shown as a confirmed one.
    const suppliers = (data.results ?? []).map((r) => ({
      ...r.product,
      match_tier: r.match_tier,
      image_score: r.image_score,
      // How the tier was decided, and why. "vision" means the two photos were
      // actually compared; "phash" means only their hashes were. Carried so the
      // row can say which — the two are not equally good evidence.
      match_basis: r.match_basis,
      match_note: r.match_note,
      match_confidence: r.match_confidence,
      supplier: r.supplier ?? null,
      seller_name: r.supplier?.company_name ?? r.product.seller_name,
      // The row renders `email` / `phone` / `whatsapp` directly. Without these
      // three lines a supplier that *does* publish a contact still showed a
      // blank cell, because the profile's lists were never unpacked onto the
      // shape the table reads.
      email: r.supplier?.emails?.[0] ?? null,
      phone: r.supplier?.phones?.[0] ?? null,
      whatsapp: r.supplier?.whatsapp?.[0] ?? null,
      // The named person on the account. Often the only human handle these
      // marketplaces publish — the address behind it is served encrypted.
      contact_name: r.supplier?.contact_name ?? null,
      years_active: r.supplier?.years_active ?? null,
      business_type: r.supplier?.business_type ?? null,
      pages_scanned: r.supplier?.pages_scanned ?? 0,
      contact_type: r.supplier?.emails?.length ? "direct" : r.product.contact_type,
      contact_value: r.supplier?.emails?.[0] ?? r.product.contact_value,
    }));
    if (suppliers.length) groups.push({ product, suppliers });
  }

  return { groups, warnings };
}

// --- Lens Sourcing (Method 2) ----------------------------------------------
//
// The browserless path behind POST /api/find-suppliers: Google Lens finds the
// product page, Oxylabs opens it for the supplier, price and MOQ. Seconds
// rather than the minutes searchManufacturers takes, because nothing drives a
// cloud browser through an upload widget.
//
// What it will not do is pretend to be the other pipeline. A Lens row carries
// no match tier: nothing compared the two products, so `match_basis` is
// "lens" and the badge says so. Presenting a Lens hit as a verified match
// would be exactly the confident-looking wrong answer the tiering exists to
// prevent. See CONTEXT.md's Lens Match Confidence entry.

// SupplierMatch.price is a string when it came from SerpApi untouched, and a
// {min,max,currency} object when it was read off the supplier's own quantity
// ladder. The ladder is the honest one — Alibaba's single advertised price is
// the rate at five thousand units, not at the MOQ.
const CURRENCY_SYMBOL = { USD: "$", CNY: "¥", EUR: "€", GBP: "£", INR: "₹", RSD: "RSD " };

function formatLensPrice(price) {
  if (!price) return { text: null, min: null, max: null, currency: null };
  if (typeof price === "string") return { text: price, min: null, max: null, currency: null };
  const symbol = CURRENCY_SYMBOL[price.currency] ?? (price.currency ? `${price.currency} ` : "");
  const money = (n) => `${symbol}${n.toFixed(2)}`;
  return {
    text: price.max > price.min ? `${money(price.min)} - ${money(price.max)}` : money(price.min),
    min: price.min,
    max: price.max,
    currency: price.currency ?? null,
  };
}

function lensSupplierRow(match) {
  const price = formatLensPrice(match.price);
  const contacts = match.contacts ?? null;
  return {
    site: match.source,
    title: match.product_title,
    product_url: match.product_url,
    image_url: match.image_url,
    price_text: price.text,
    price_min: price.min,
    price_max: price.max,
    currency: price.currency,
    moq: match.moq != null ? String(match.moq) : null,
    seller_name: match.supplier_name,
    // What makes the company name clickable in the grid.
    seller_url: match.supplier_url ?? null,
    // Provenance, never a tier. "lens" tells MatchBadge to render the Lens
    // wording instead of borrowing the vision/phash vocabulary.
    match_basis: "lens",
    match_tier: match.match_confidence === "lens_exact_match" ? "lens_exact" : "lens_visual",
    match_note: match.enriched ? null : match.enrichment_error,
    enriched: match.enriched,
    // Contacts are only present when the request asked for them; an absent
    // object means "not looked for", which is distinct from "found none".
    email: contacts?.emails?.[0] ?? null,
    phone: contacts?.phones?.[0] ?? null,
    whatsapp: contacts?.whatsapp?.[0] ?? null,
    contact_name: contacts?.contact_name ?? null,
    contacts,
    rating: null,
    business_type: null,
    years_active: null,
    contact_type: contacts?.emails?.length ? "direct" : "form",
    contact_value: contacts?.emails?.[0] ?? match.supplier_url ?? match.product_url,
  };
}

// Sites the deep-search fallback can actually query. Taobao is a Lens-only
// source — /api/sourcing/by-url rejects it — so it's dropped here rather than
// sent along to produce a 400.
const DEEP_SEARCH_SITES = ["alibaba", "1688", "made_in_china"];

/**
 * Find suppliers for each chosen product via Lens Sourcing.
 *
 * One request per product, like searchManufacturers — each is independent, and
 * a failure on one leaves the rest intact.
 *
 * **Falls back to the deep search when Lens finds no marketplace listing.**
 * Lens indexes what the web hosts, and for a branded US retail product it
 * mostly hosts Amazon and Walmart: a live search for Owala, HydroJug and
 * Mainstays tumblers returned 409 matches and not one on Alibaba, 1688 or
 * Taobao. That is a real coverage limit, not a bug, and the honest response to
 * it is not an empty grid — /api/sourcing/by-url searches the marketplaces'
 * *own* reverse-image indexes and finds listings Lens has never seen. So the
 * fast path runs first and the slow one covers what it missed, per product.
 */
// The fast Lens lookup, cached per product photo rather than per call. This is
// what makes the silent prefetch after a product search worth running: the
// prefetch and the user's later click ask for the same products, and without a
// cache the click would re-request — and re-bill — everything the prefetch had
// already fetched, arriving no sooner than if nothing had been prefetched.
//
// Keyed on the image URL because that is the entire input to the request. The
// promise is cached, not the result, so a click landing mid-prefetch joins the
// request in flight instead of starting a second one.
const _supplierCache = new Map();

function _lookupSuppliers(product, includeContacts) {
  const key = `${product.image_url}|${includeContacts ? 1 : 0}`;
  const hit = _supplierCache.get(key);
  if (hit) return hit;

  // Deliberately not given the caller's `signal`: a cached lookup is shared, so
  // one caller aborting must not cancel a request another is waiting on. Callers
  // check their own signal after awaiting (see runSupplierSearch).
  const promise = fetch(`${API_BASE}/api/find-suppliers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_url: product.image_url, include_contacts: includeContacts }),
  }).then(handleResponse);

  _supplierCache.set(key, promise);
  // A failure is never cached: the fast and deep paths use different vendors and
  // fail independently, so a retry is frequently the fix.
  promise.catch(() => _supplierCache.delete(key));
  return promise;
}

// Drops everything prefetched. Called when a new product search starts, so a
// stale set of products can't hold memory or serve a previous query's suppliers.
export function clearSupplierCache() {
  _supplierCache.clear();
  _deepCache.clear();
}

export async function findSuppliersByImage(
  products,
  { mfrSites = [], includeContacts = false, signal, deepFallback = true, onDeepSearch } = {}
) {
  const targets = products.filter((p) => p.image_url);
  const skipped = products.length - targets.length;

  const settled = await Promise.all(
    targets.map(async (product) => {
      try {
        return { product, data: await _lookupSuppliers(product, includeContacts) };
      } catch (error) {
        if (error.name === "AbortError") throw error;
        return { product, error };
      }
    })
  );

  const groups = [];
  const warnings = [];
  const missed = [];
  let totalMs = 0;
  if (skipped > 0) {
    warnings.push(`${skipped} product(s) had no photo to search with and were skipped.`);
  }

  for (const { product, data, error } of settled) {
    if (error) {
      // A product whose fast search failed outright is still worth the deep
      // one — the two use different vendors and fail independently.
      missed.push(product);
      warnings.push(`[${product.title?.slice(0, 40) ?? "product"}] ${error.message}`);
      continue;
    }
    // Operator-facing faults (bad Oxylabs credentials) travel separately from
    // ordinary partial-result notes and must not be filtered away as noise.
    warnings.push(...(data.errors ?? []));
    warnings.push(...(data.warnings ?? []));
    totalMs = Math.max(totalMs, data.latency_ms ?? 0);

    let suppliers = (data.results ?? []).map(lensSupplierRow);
    // The site picker filters what Lens returned rather than what it searched:
    // Lens has no per-site query to narrow, so narrowing happens here.
    if (mfrSites.length) suppliers = suppliers.filter((s) => mfrSites.includes(s.site));
    if (suppliers.length) groups.push({ product, suppliers });
    else missed.push(product);
  }

  // `signal` cannot cancel the requests themselves — they are cached and shared,
  // so no one caller owns them — but it does stop a search the user has already
  // abandoned from opening a fresh round of browser sessions here.
  if (deepFallback && missed.length && !signal?.aborted) {
    const sites = mfrSites.length
      ? DEEP_SEARCH_SITES.filter((s) => mfrSites.includes(s))
      : DEEP_SEARCH_SITES;
    const deepSites = sites.length ? sites : DEEP_SEARCH_SITES;
    // The deep search takes minutes where the fast one took seconds, so the
    // caller is told to change its label — a progress bar pinned at 100% for
    // two minutes reads as a hang, not as a slower search still running.
    //
    // Only the products actually being waited on are counted. When the prefetch
    // has already driven these searches the answers come straight back out of
    // the cache, and announcing a slow marketplace search that isn't happening
    // is just a wrong message shown for one frame.
    const pending = missed.filter((p) => _deepPending(p, deepSites));
    if (pending.length) onDeepSearch?.(pending.length);
    const deep = await searchManufacturers(missed, { mfrSites: deepSites });
    groups.push(...deep.groups);
    warnings.push(...deep.warnings);
    warnings.push(
      deep.groups.length
        ? `Google Lens found no marketplace listing for ${missed.length} product(s), so those ` +
          `were searched again against the marketplaces' own image indexes — ${deep.groups.length} ` +
          `came back with suppliers.`
        : `Google Lens found no marketplace listing for ${missed.length} product(s), and ` +
          `searching the marketplaces' own image indexes found none either.`
    );
  }

  return { groups, warnings, latencyMs: totalMs };
}

export async function searchByImage(
  file,
  { sites = [], detectedItem = null, inspirationImageUrl = null, includeLens = false, signal } = {}
) {
  const formData = new FormData();
  formData.append("file", file);
  const params = new URLSearchParams();
  if (sites.length) params.set("sites", sites.join(","));
  if (detectedItem) params.set("detected_item", detectedItem);
  if (inspirationImageUrl) params.set("inspiration_image_url", inspirationImageUrl);
  if (includeLens) params.set("include_lens", "true");
  const response = await fetch(`${API_BASE}/api/search/image?${params.toString()}`, {
    method: "POST",
    body: formData,
    signal,
  });
  return handleResponse(response);
}

export async function searchGoogleLens(file, { detectedItem = null, inspirationImageUrl = null, signal } = {}) {
  const formData = new FormData();
  formData.append("file", file);
  const params = new URLSearchParams();
  if (detectedItem) params.set("detected_item", detectedItem);
  if (inspirationImageUrl) params.set("inspiration_image_url", inspirationImageUrl);
  const response = await fetch(`${API_BASE}/api/trending/search-lens?${params.toString()}`, {
    method: "POST",
    body: formData,
    signal,
  });
  return handleResponse(response);
}

// Zyte Lens Bridge extension — see extension/README.md. Runs a Google Lens
// search in a real, unautomated browser tab (bypasses the bot-detection that
// blocks server-side scraping) and messages results back to this page. Fails
// soft: if the extension isn't installed, this just resolves to no results
// rather than erroring the whole search.
const LENS_EXTENSION_ID = "eljofghojhhdoajgdbnibkifalnjjacd";
const LENS_EXTENSION_TIMEOUT_MS = 60000;

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("Could not read file"));
    reader.readAsDataURL(file);
  });
}

export async function searchViaLensExtension(file, { signal } = {}) {
  const empty = { results: [], warnings: [] };

  if (typeof chrome === "undefined" || !chrome.runtime?.sendMessage) {
    return empty;
  }
  if (signal?.aborted) return empty;

  let imageDataUrl;
  try {
    imageDataUrl = await fileToDataUrl(file);
  } catch {
    return empty;
  }

  const response = await Promise.race([
    new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(
          LENS_EXTENSION_ID,
          { type: "SEARCH_LENS", imageDataUrl },
          (res) => {
            // chrome.runtime.lastError is set if the extension isn't installed
            // or doesn't allow this origin — treat both as "not available."
            resolve(chrome.runtime.lastError ? null : res);
          }
        );
      } catch {
        resolve(null);
      }
    }),
    new Promise((resolve) => setTimeout(() => resolve(null), LENS_EXTENSION_TIMEOUT_MS)),
    ...(signal ? [new Promise((resolve) => signal.addEventListener("abort", () => resolve(null)))] : []),
  ]);

  if (!response || !response.ok || signal?.aborted) {
    return empty;
  }

  const results = (response.results || []).map((r) => ({
    site: "google_lens_extension",
    title: r.title,
    product_url: r.link,
  }));

  if (!results.length) {
    return { results, warnings: [] };
  }

  // Fill in image/price via Zyte's AI product extraction — best-effort, so a
  // failure here shouldn't drop the (already found) title/link results.
  try {
    const enriched = await enrichWithZyte(results, { signal });
    return { results: enriched.results, warnings: enriched.warnings || [] };
  } catch {
    return { results, warnings: [] };
  }
}

export async function enrichWithZyte(items, { signal } = {}) {
  if (!items.length) return { results: [], warnings: [] };
  const response = await fetch(`${API_BASE}/api/trending/enrich`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      items: items.map((it) => ({ title: it.title, product_url: it.product_url, site: it.site })),
    }),
    signal,
  });
  return handleResponse(response);
}

export async function searchPinterest(idea, n = 20) {
  const response = await fetch(`${API_BASE}/api/trending/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ idea, n }),
  });
  return handleResponse(response);
}

export async function detectItems(imageUrl) {
  const response = await fetch(`${API_BASE}/api/trending/detect`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_url: imageUrl }),
  });
  return handleResponse(response);
}

export async function detectItemsFromUpload(file) {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE}/api/trending/detect-upload`, {
    method: "POST",
    body: formData,
  });
  return handleResponse(response);
}

export async function fetchInspirationImageAsFile(imageUrl) {
  const params = new URLSearchParams({ url: imageUrl });
  const response = await fetch(`${API_BASE}/api/trending/fetch-image?${params.toString()}`);
  if (!response.ok) throw new Error(`Could not load image (${response.status})`);
  const blob = await response.blob();
  return new File([blob], "inspiration.jpg", { type: "image/jpeg" });
}

export function cropUrl(cropId) {
  return `${API_BASE}/api/trending/crop/${cropId}`;
}

export async function fetchCropAsFile(cropId) {
  const response = await fetch(cropUrl(cropId));
  if (!response.ok) throw new Error(`Could not load crop (${response.status})`);
  const blob = await response.blob();
  return new File([blob], `${cropId}.jpg`, { type: "image/jpeg" });
}
