export const SITES = [
  { id: "alibaba", label: "Alibaba", color: "#ff6a00" },
  { id: "aliexpress", label: "AliExpress", color: "#e62e04" },
  { id: "made_in_china", label: "Made-in-China", color: "#c0111a" },
];

// Product Search sources. Exactly the twelve the backend can actually reach —
// listing a site here that bestsellers.py can't serve would promise a source
// the app never returns. See backend/app/bestsellers.py SITES.
export const BESTSELLER_SITES = [
  { id: "amazon", label: "Amazon", color: "#ff9900" },
  { id: "walmart", label: "Walmart", color: "#0071dc" },
  { id: "temu", label: "Temu", color: "#fb7701" },
  { id: "costco", label: "Costco", color: "#e31837" },
  { id: "ikea", label: "IKEA", color: "#0058a3" },
  // Added once each had a live Apify actor behind it — see
  // backend/app/apify_retail.py, which records what every one of them returns.
  { id: "target", label: "Target", color: "#cc0000" },
  { id: "ebay", label: "eBay", color: "#e53238" },
  { id: "etsy", label: "Etsy", color: "#f56400" },
  { id: "wayfair", label: "Wayfair", color: "#7b189f" },
  { id: "bestbuy", label: "Best Buy", color: "#0046be" },
  { id: "homedepot", label: "Home Depot", color: "#f96302" },
  // Not a store: the keyword is answered by picture, via Pinterest images run
  // through Google Lens, then narrowed to the listings whose titles describe
  // what was searched for. See backend/app/google_shopping.py. It sits with the
  // stores because it is picked the same way — a source pill on Product Search.
  { id: "google_shopping", label: "Google Shopping", color: "#4285f4" },
];

// Stores on the roadmap, shown so the range is visible but marked and not
// selectable — the backend has no scraper for any of them, and a pill that
// selects a source the search can't reach returns an empty store with an
// excuse. Clicking one says "coming soon" instead. Move an entry up into
// BESTSELLER_SITES the day bestsellers.py can serve it, and not before.
export const COMING_SOON_SITES = [];

// What the Product Search store picker renders: the live five, then the rest.
export const PRODUCT_SEARCH_SITES = [...BESTSELLER_SITES, ...COMING_SOON_SITES];

// Manufacturer sources — where the "Search for manufacturers" button pulls
// supplier listings from, for the products the user picks.
//
// Exactly the three the backend enriches (lens_suppliers.ENRICH_SITES) and the
// three the deep search can query (api.js DEEP_SEARCH_SITES): a listing from any
// of them arrives with a price and MOQ read off the page, not on Lens data
// alone. Taobao was here and isn't any more — it is a consumer marketplace, the
// pipeline can't read a price or MOQ off it, and over the 36 searches in the
// backend's Lens cache it produced a single hit against Made-in-China's 19.
export const MANUFACTURER_SITES = [
  { id: "alibaba", label: "Alibaba", color: "#ff6a00" },
  { id: "1688", label: "1688", color: "#ff5000" },
  { id: "made_in_china", label: "Made-in-China", color: "#c0111a" },
];

// Not a filterable scraper site (so it's kept out of SITES/SiteFilter) — Google
// Lens results only ever come from the trending search-lens endpoint — but it
// still needs a badge label/color in ResultsGrid like any other source.
const EXTRA_BADGES = {
  google_lens: { label: "Google Lens", color: "#4285f4" },
  google_lens_exact: { label: "Google Lens · Exact Match", color: "#0f9d58" },
  google_lens_extension: { label: "Google Lens (Browser)", color: "#673ab7" },
};

const ALL_SITES = [...SITES, ...BESTSELLER_SITES, ...COMING_SOON_SITES, ...MANUFACTURER_SITES];
export const SITE_LABELS = {
  ...Object.fromEntries(ALL_SITES.map((s) => [s.id, s.label])),
  ...Object.fromEntries(Object.entries(EXTRA_BADGES).map(([id, b]) => [id, b.label])),
};
export const SITE_COLORS = {
  ...Object.fromEntries(ALL_SITES.map((s) => [s.id, s.color])),
  ...Object.fromEntries(Object.entries(EXTRA_BADGES).map(([id, b]) => [id, b.color])),
};

// Google Lens hits are web pages, not marketplace listings — they never have
// a real "seller" or contact channel, so UI/export code hides that section
// for them rather than showing empty/misleading seller-shaped data.
export const LENS_SITES = new Set(["google_lens", "google_lens_exact", "google_lens_extension"]);
export function isLensSite(site) {
  return LENS_SITES.has(site);
}

// Retail product sources (Product Search) vs. everything else (manufacturer/
// sourcing listings). Retail cards show rating and hide the seller-contact
// block; manufacturer/sourcing cards show seller name, MOQ and a contact link.
const RETAIL_SITES = new Set(BESTSELLER_SITES.map((s) => s.id));
export function isRetailSite(site) {
  return RETAIL_SITES.has(site);
}

// Google Lens hits come back with a real destination URL but a generic
// "google_lens" site. Map the URL's host to one of our retail site ids so a
// reverse-image result can be attributed to (and filtered by) the site it
// actually lives on. Returns null when the host isn't a site we track.
const URL_SITE_PATTERNS = [
  ["amazon.", "amazon"],
  ["walmart.", "walmart"],
  ["temu.", "temu"],
  ["pinterest.", "pinterest"],
  ["costco.", "costco"],
  ["ikea.", "ikea"],
  ["target.", "target"],
  ["ebay.", "ebay"],
  ["etsy.", "etsy"],
  ["wayfair.", "wayfair"],
  ["bestbuy.", "bestbuy"],
  ["homedepot.", "homedepot"],
];
export function siteForUrl(url) {
  if (!url) return null;
  let host;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    host = String(url).toLowerCase();
  }
  for (const [frag, id] of URL_SITE_PATTERNS) {
    if (host.includes(frag)) return id;
  }
  return null;
}
