from typing import Literal, Optional, Union

from pydantic import BaseModel


class Seller(BaseModel):
    site: str = ""
    seller_name: Optional[str] = None
    seller_url: Optional[str] = None
    product_url: str
    price_text: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    currency: Optional[str] = None
    moq: Optional[str] = None
    contact_type: Optional[Literal["direct", "form"]] = None
    contact_value: Optional[str] = None


class Product(BaseModel):
    site: str = ""
    title: str
    image_url: Optional[str] = None
    price_text: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    currency: Optional[str] = None
    moq: Optional[str] = None
    seller_name: Optional[str] = None
    seller_url: Optional[str] = None
    contact_type: Optional[Literal["direct", "form"]] = None
    contact_value: Optional[str] = None
    product_url: str
    detected_item: Optional[str] = None
    inspiration_image_url: Optional[str] = None
    image_match: Optional[float] = None
    sellers: list[Seller] = []

    # Best Seller Search fields (see CONTEXT.md). site_rank is the 1-based
    # position within a site's own "best selling" sort; popularity_score is the
    # rating x review_count fallback used when the site has no such sort.
    # normalized_score is the 0-1 cross-site-comparable value both collapse to;
    # combined_rank is the final position in the merged top-100.
    rating: Optional[float] = None
    review_count: Optional[int] = None
    site_rank: Optional[int] = None
    popularity_score: Optional[float] = None
    normalized_score: Optional[float] = None
    combined_rank: Optional[int] = None
    identifier: Optional[str] = None  # the Shared Identifier used for merging, if any
    # Which signal actually produced normalized_score on this listing's site:
    # "bestseller_sort" | "sold_count" | "rating" | "relevance". Carried through
    # to the UI so a relevance-ordered row is never presented as a best seller.
    rank_basis: Optional[str] = None

    # What the Claude relevance agent judged this row to be against the query:
    # "match" | "variant" | "accessory" | "unrelated" (app/claude_agent.py).
    # None means it was never screened — no key, agent off, or a failed batch —
    # which is deliberately distinct from "screened and found relevant".
    relevance: Optional[str] = None


class SearchResponse(BaseModel):
    results: list[Product]
    warnings: list[str] = []


class InspirationImage(BaseModel):
    image_url: str
    pin_url: Optional[str] = None
    title: Optional[str] = None


class PinterestSearchResponse(BaseModel):
    images: list[InspirationImage]


class DetectedItem(BaseModel):
    label: str
    score: float
    box: list[float]
    crop_id: str


class DetectResponse(BaseModel):
    items: list[DetectedItem]


class SupplierProfile(BaseModel):
    """Company-level facts pulled from a supplier's own page on the marketplace
    (stage 3 of the sourcing pipeline). Every field is Optional and stays None
    when the page doesn't publicly show it — an absent field means "not
    published", never a guess, consistent with how contact_type is handled on
    Product."""

    site: str = ""
    supplier_url: str
    company_name: Optional[str] = None
    location: Optional[str] = None
    years_active: Optional[int] = None
    business_type: Optional[str] = None  # manufacturer / trading company / ...
    verified: Optional[bool] = None
    # The named person on the account ("Ms. zhao"). Published where the email
    # and phone are not — Alibaba serves the name in clear and the identity
    # behind it as `contactEncryptId`, so this is often the only human handle
    # available before an enquiry is sent.
    contact_name: Optional[str] = None
    emails: list[str] = []
    phones: list[str] = []
    whatsapp: list[str] = []
    # Which pages of the company's site were actually read to build this, so a
    # thin profile can be told apart from a thinly-published supplier.
    pages_scanned: int = 0
    # Why enrichment failed, when it did — surfaced rather than swallowed.
    warning: Optional[str] = None


class SourcingResult(BaseModel):
    """One supplier listing found from a photo, plus the company behind it."""

    product: Product
    supplier: Optional[SupplierProfile] = None
    # 0-1 visual similarity between the query photo and this listing's thumbnail.
    image_score: Optional[float] = None
    # "identical" | "exact" | "similar" — see sourcing.py for the evidence each tier needs.
    match_tier: Optional[str] = None
    # How match_tier was decided: "vision" (a Claude verdict on the two photos)
    # or "phash" (perceptual hash distance alone). Shown rather than inferred,
    # for the same reason Product.rank_basis is — the two are not equally strong
    # and a tier that came from hashing should not read as a confirmed match.
    match_basis: Optional[str] = None
    # The vision agent's own words on what decided it, when it ran.
    match_note: Optional[str] = None
    match_confidence: Optional[float] = None


class SourcingResponse(BaseModel):
    results: list[SourcingResult]
    warnings: list[str] = []
    # Per-site breadcrumb of what actually happened, so a thin result set can be
    # read as "site X was challenged" instead of "no suppliers exist".
    site_status: dict[str, str] = {}


# --- Lens Sourcing (POST /api/find-suppliers, app/lens_suppliers.py) --------
# A separate, faster route to the same question the models above answer. Kept
# as its own schema rather than folded into SourcingResult because the evidence
# behind a row is genuinely different: there is no phash distance and no vision
# verdict here, only "Google Lens matched this picture", and reusing match_tier
# would let a Lens hit render in the UI as a confirmed visual match.


class PriceRange(BaseModel):
    """A quoted range, once it has been parsed off a product page. The
    alternative on SupplierMatch.price is a bare string — which is what a row
    that was never enriched carries, straight from SerpApi. Which of the two
    arrives is itself the signal for how much the number has been checked."""

    min: float
    max: float
    currency: Optional[str] = None


class SupplierContacts(BaseModel):
    """What a supplier's own site publishes, read by both regex and a Claude
    agent that also *looks at the pictures*.

    The image pass is the point. Contact details on these minisites are
    routinely baked into banner and business-card graphics — which is partly
    how they survive text scraping — so a text-only scan reports "publishes
    nothing" for a supplier whose phone number is sitting in a JPEG at the top
    of their homepage. Every value here is transcribed from something actually
    on the page; nothing is completed, corrected or inferred."""

    emails: list[str] = []
    phones: list[str] = []
    whatsapp: list[str] = []
    wechat: list[str] = []
    contact_name: Optional[str] = None
    # Where each channel was found: "text" | "image" | "both". A number read out
    # of a JPEG is a weaker claim than one in a mailto: link — it went through
    # OCR — and the caller should be able to tell them apart.
    found_in: dict[str, str] = {}
    pages_scanned: int = 0
    images_read: int = 0
    # Why nothing was found, when nothing was. "We looked and they publish
    # none" and "we could not look" are different facts.
    warning: Optional[str] = None

    def is_empty(self) -> bool:
        return not (self.emails or self.phones or self.whatsapp or self.wechat)


class SupplierMatch(BaseModel):
    """One Chinese-marketplace listing Google Lens matched to the query photo."""

    supplier_name: Optional[str] = None
    # The supplier's own company page — what makes supplier_name clickable.
    # Absent when the product page didn't link one, never guessed at from the
    # company's name.
    supplier_url: Optional[str] = None
    # Populated only when the request asks for it (include_contacts), because
    # each one costs several page fetches and a vision call. None means "not
    # looked for"; a present object with empty lists means "looked, found none".
    contacts: Optional[SupplierContacts] = None
    product_title: str
    price: Optional[Union[PriceRange, str]] = None
    moq: Optional[Union[str, int]] = None
    product_url: str
    image_url: Optional[str] = None
    source: str  # "alibaba" | "1688" | "taobao"
    # Provenance, never a score. "lens_exact_match" means Lens found the
    # pixel-identical image on this page; "lens_visual_match" means it merely
    # looks like it. Nothing in this pipeline compares the two products, so no
    # row here may claim more than "Lens put these together".
    match_confidence: str = "lens_visual_match"
    # False => every field above came from SerpApi's inline data and the product
    # page was never opened. Shown rather than inferred, so a thin row reads as
    # "not enriched" instead of "this supplier publishes nothing".
    enriched: bool = False
    # Why enrichment didn't happen, when it didn't. Surfaced per row rather than
    # collapsed into one warning, because it is usually only some of them.
    enrichment_error: Optional[str] = None


class PartialMatch(BaseModel):
    """A Lens hit that is not on a Chinese marketplace. Returned for context —
    a retail listing of the same product tells the user what it sells for, and
    on the days Lens has no B2B coverage for a category it is the only thing
    standing between them and an empty response."""

    title: str
    product_url: str
    image_url: Optional[str] = None
    price: Optional[str] = None
    source_domain: str
    source_name: Optional[str] = None
    match_confidence: str = "lens_visual_match"


class StepTimings(BaseModel):
    lens_ms: int
    enrichment_ms: int
    # Present only when the request asked for supplier contact details. Kept
    # separate because it is an order of magnitude heavier than the two steps
    # above and would otherwise make enrichment look like the slow one.
    contacts_ms: Optional[int] = None
    # Present only when an uploaded image had to be published somewhere Google
    # could fetch it. It is part of step 1's wall clock and the single most
    # likely thing to blow the latency budget, so it is timed separately rather
    # than hidden inside lens_ms.
    upload_ms: Optional[int] = None


class FindSuppliersResponse(BaseModel):
    query_image: str
    results: list[SupplierMatch]
    partial_matches: list[PartialMatch] = []
    latency_ms: int
    step_timings: StepTimings
    # Step 1 came from the 30-day cache, so lens_ms is a disk read and the
    # candidate list is up to a month old. Reported, not glossed over.
    cached: bool = False
    cache_age_days: Optional[int] = None
    warnings: list[str] = []
    # Faults an operator has to act on — bad Oxylabs credentials above all —
    # kept apart from warnings, which are ordinary partial-result notes.
    errors: list[str] = []
