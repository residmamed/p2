// Which backend warnings a person should actually see.
//
// The sourcing pipeline emits two very different kinds of message on the same
// `warnings` array. One kind tells the user something about their result — a
// site found nothing, listings couldn't be verified, suppliers publish no
// contact details. The other is the pipeline narrating its own plumbing:
//
//   [1688] file attach failed: No file input matched 'input[type=file]'
//   [1688] retrying upload (1/3)
//   [Alibaba] site parser found nothing — falling back to Zyte extraction
//   [Made-in-China] upload accepted but no results URL appeared
//
// Forty lines of that shipped straight to the screen above the results. It is
// genuinely useful in a terminal and worthless on a product page: the user
// cannot act on a CSS selector, and burying the two or three real messages in
// retry chatter means the real ones don't get read either.
//
// So the noise is dropped and the sites it concerned are summarised in one
// line. Dropped, not hidden behind a toggle — this is diagnostics, and the
// place for it is the server log, where it still is.
//
// The rule is a denylist rather than an allowlist on purpose. A warning nobody
// anticipated should reach the user by default; the alternative is silently
// swallowing a real problem because it didn't match a pattern.

// Pipeline-internal chatter. Each of these describes *how* the scrape went,
// never what the user got.
const INTERNAL_PATTERNS = [
  /file attach failed/i,
  /no file input matched/i,
  /retrying upload/i,
  /upload accepted but no results url/i,
  /results never rendered/i,
  /site parser found nothing/i,
  /falling back to/i,
  /results page held no parseable listings/i,
  /parser error/i,
  /productList failed/i,
  /could not fetch the results page/i,
  /image search could not be completed/i,
  /upload failed/i,
  /^\[[^\]]+\]\s*$/,
];

// "[Alibaba] something" -> "Alibaba"
const SITE_PREFIX_RE = /^\[([^\]]+)\]/;

export function isInternal(warning) {
  return INTERNAL_PATTERNS.some((p) => p.test(warning));
}

function siteOf(warning) {
  const match = SITE_PREFIX_RE.exec(warning);
  return match ? match[1] : null;
}

/**
 * Split raw backend warnings into what to show and what to swallow.
 *
 * Returns { shown, hiddenCount, failedSites }. `shown` keeps the user-facing
 * messages in their original order; `failedSites` names the sites whose only
 * messages were internal failures, so the caller can say "these couldn't be
 * searched" once instead of thirty times.
 */
export function partitionWarnings(warnings = []) {
  const shown = [];
  const failedSites = new Set();
  let hiddenCount = 0;

  for (const raw of warnings) {
    const warning = String(raw ?? "").trim();
    if (!warning) continue;
    if (isInternal(warning)) {
      hiddenCount += 1;
      const site = siteOf(warning);
      if (site) failedSites.add(site);
      continue;
    }
    // The same message arrives once per product searched; the user needs it once.
    if (!shown.includes(warning)) shown.push(warning);
  }

  return { shown, hiddenCount, failedSites: [...failedSites] };
}

/**
 * The user-facing warning list: real messages, plus at most one line standing
 * in for everything that was dropped.
 */
export function userWarnings(warnings, t) {
  const { shown, hiddenCount, failedSites } = partitionWarnings(warnings);
  if (!hiddenCount) return shown;
  // Naming the sites keeps this honest — a thin result set still reads as
  // "these sites didn't answer", not as "no suppliers exist".
  return failedSites.length
    ? [...shown, t("sitesUnavailable", failedSites.join(", "))]
    : shown;
}
