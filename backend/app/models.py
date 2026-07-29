from typing import Literal, Optional

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
