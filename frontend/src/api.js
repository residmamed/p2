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
export const MFR_SEARCH_MS = 120000;

// Product Search — real retail results for the selected sources, ranked by each
// site's best available demand signal (see backend bestsellers.py). Every
// product carries a `rank_basis` saying which signal produced its position.
export async function searchBestSellers(query, { sites = [], signal } = {}) {
  const params = new URLSearchParams({ q: query });
  if (sites.length) params.set("sites", sites.join(","));
  const response = await fetch(`${API_BASE}/api/bestsellers?${params.toString()}`, { signal });
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
export async function searchManufacturers(products, { mfrSites = [], signal } = {}) {
  const targets = products.filter((p) => p.image_url);
  const skipped = products.length - targets.length;

  const settled = await Promise.all(
    targets.map(async (product) => {
      const params = new URLSearchParams({ image_url: product.image_url });
      if (mfrSites.length) params.set("sites", mfrSites.join(","));
      try {
        const response = await fetch(`${API_BASE}/api/sourcing/by-url?${params.toString()}`, {
          method: "POST",
          signal,
        });
        const data = await handleResponse(response);
        return { product, data };
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
