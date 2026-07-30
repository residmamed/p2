// Which supplier rows are worth showing as "this company sells your product".
//
// The backend deliberately returns everything the sites gave back, tiered
// rather than filtered, because "same product" and "same category" are
// different answers and collapsing them hides the difference. That is the right
// contract for an API. It is the wrong default for a screen: a list of 40 rows
// where 17 are confirmed and 27 are "we couldn't verify this" reads as 40
// suppliers for your product, and the user contacts the wrong ones.
//
// So the grid shows the rows something actually vouched for:
//
//   vision + identical/exact   a model compared the two photographs and said
//                              it is the same product. The strong claim.
//   phash identical            the supplier is using the buyer's exact image
//                              file. Different evidence, equally conclusive.
//
// Everything else — `similar`, `unverified`, and any tier that only a hash
// produced — is held back, not deleted. See CONTEXT.md's Match Basis entry for
// why a phash tier is the weaker claim: on live runs every genuine match
// between a retail studio photo and a factory catalogue photo landed past the
// hash thresholds entirely, so hash silence means "not looked at", never
// "rejected".
//
// **Never an empty grid.** If nothing was confirmed, the unconfirmed rows are
// shown with a warning saying so, exactly as before. Hiding a real supplier
// because no verifier got to it is the one failure this must not introduce —
// filtering on hash distance alone is what produced an empty grid the last
// time it was tried.

const CONFIRMED_TIERS = new Set(["identical", "exact"]);

export function isConfirmedSeller(supplier) {
  const tier = supplier?.match_tier;
  if (!tier) return false;
  // Lens Sourcing has no tiers, only provenance. An exact match means Google
  // Lens found the pixel-identical image file hosted on that supplier's page,
  // which is strong evidence they are selling that product; a visual match
  // means it merely resembles it, which is a lead and not a seller.
  //
  // A link to the listing is required as well — that is now the whole content
  // of the row, and one without it has nothing to open.
  //
  // This used to require the *company name* instead, back when the grid led with
  // it: a row whose enrichment couldn't read a supplier meant an empty company
  // cell. The grid leads with the listing now, so that gate was throwing away
  // real listings on Alibaba and Made-in-China for the sake of a column that no
  // longer exists.
  if (supplier.match_basis === "lens") {
    return tier === "lens_exact" && !!supplier.product_url;
  }
  if (supplier.match_basis === "vision") return CONFIRMED_TIERS.has(tier);
  // A hash can only ever prove the reused-file case; it cannot recognise a
  // re-shot product, so `exact` from a hash is not treated as confirmation.
  return tier === "identical";
}

/**
 * Split one product's suppliers into the ones to show and the ones to hold.
 *
 * Returns { sellers, unconfirmed, confirmedOnly }. `confirmedOnly` is false
 * when nothing could be confirmed and `sellers` therefore falls back to the
 * full list — the caller uses it to explain why the rows look weaker.
 */
export function splitSuppliers(suppliers = []) {
  const sellers = suppliers.filter(isConfirmedSeller);
  const unconfirmed = suppliers.filter((s) => !isConfirmedSeller(s));
  if (!sellers.length) {
    return { sellers: suppliers, unconfirmed: [], confirmedOnly: false };
  }
  return { sellers, unconfirmed, confirmedOnly: true };
}
