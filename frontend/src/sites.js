export const SITES = [
  { id: "alibaba", label: "Alibaba", color: "#ff6a00" },
  { id: "aliexpress", label: "AliExpress", color: "#e62e04" },
  { id: "made_in_china", label: "Made-in-China", color: "#c0111a" },
];

// Product Search sources. Exactly the five the backend can actually reach —
// listing a site here that bestsellers.py can't serve would promise a source
// the app never returns. See backend/app/bestsellers.py SITES.
export const BESTSELLER_SITES = [
  { id: "amazon", label: "Amazon", color: "#ff9900" },
  { id: "walmart", label: "Walmart", color: "#0071dc" },
  { id: "temu", label: "Temu", color: "#fb7701" },
  { id: "costco", label: "Costco", color: "#e31837" },
  { id: "ikea", label: "IKEA", color: "#0058a3" },
];

// Manufacturer sources — where the "Search for manufacturers" button pulls
// supplier listings from, for the products the user picks.
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

const ALL_SITES = [...SITES, ...BESTSELLER_SITES, ...MANUFACTURER_SITES];
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
  ["shein.", "shein"],
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
