"""Stage 2.5: open the product page and find out who actually sells it.

This is the stage whose absence made the sourcing grid return products with no
suppliers. The chain that produced it:

    Alibaba image search returns inline HTML
      -> parse_alibaba finds nothing (it reads the *text*-search `_offer_list`
         blob; the image-results page has a different shape)
      -> extraction falls back to Zyte `productList`
      -> productList returns name/url/price/image and, by design, refuses to
         invent seller identity (see sourcing._from_product_list)
      -> seller_url is None on every row
      -> stage 3 enrichment has nothing to enrich
      -> "No supplier company pages were linked from these listings"

Measured on a live Alibaba run: 49 listings, 0 with any seller field. The
listings were fine. Nobody had opened them.

A search-results card is a *product* card — it names the product, not the
company behind it. The company is one click away on the product page, and
everything needed is there in one Zyte call that asks for `product` and
`browserHtml` together:

    product.brand.name           the manufacturer, structured, no parsing
    product.additionalProperties MOQ, model number, place of origin
    browserHtml                  the company's own minisite URL

The minisite URL is the valuable one: it is the handle `supplier_profile.enrich`
already knows how to turn into emails, phones, years active and verification
badges. Filling it in is what makes stage 3 start working.

Cost is the reason this is capped and runs last. One page fetch per listing at
~38s each, so it runs only on the top rows — and after visual matching, so the
budget is spent on listings a vision model already confirmed are the buyer's
product rather than on whatever the site happened to return first.
"""

import asyncio
import re

from .models import Product, SupplierProfile
from .zyte_client import ZyteClient, ZyteError

# Per-site pattern for the seller's own company page.
#
# `alibaba` is confirmed against live product pages, and needs both hosts: the
# minisite is a subdomain that is `<seller>.en.alibaba.com/company_profile` for
# some sellers and `<seller>.trustpass.alibaba.com/company_profile.html` for
# others. Probing only the first left 1 of 4 live listings named-but-unlinked,
# which is exactly the silent half-failure this module exists to remove. The
# subdomain character class deliberately excludes a dot so the mobile
# `<seller>.m.trustpass...` variant doesn't match in place of the desktop page.
#
# The rest are written from each site's documented URL shape and are NOT
# confirmed end-to-end, following the same convention as
# image_discovery.VERIFIED: an unverified pattern that misses costs the company
# URL for that site, not the run. The company *name* still arrives via
# brand.name, so a miss degrades to "named but not contactable".
COMPANY_URL_PATTERNS: dict[str, re.Pattern] = {
    "alibaba": re.compile(
        r"https?://[a-z0-9\-]+\.(?:en|trustpass)\.alibaba\.com/company_profile(?:\.html)?", re.I
    ),
    "made_in_china": re.compile(r"https?://[a-z0-9\-]+\.en\.made-in-china\.com/(?:company|aboutus)[^\"'\s]*", re.I),
    "1688": re.compile(r"https?://(?:shop|winport)[a-z0-9\-]*\.1688\.com[^\"'\s]*", re.I),
    "aliexpress": re.compile(r"https?://(?:www\.)?aliexpress\.com/store/\d+", re.I),
}

VERIFIED = {
    "alibaba": "live-verified 2026-07-28 (brand.name + .en./.trustpass. company_profile; 4/4 named, 4/4 linked)",
    "made_in_china": "UNVERIFIED — URL shape only; confirm before trusting",
    "1688": "UNVERIFIED — URL shape only; confirm before trusting",
    "aliexpress": "UNVERIFIED — brand.name on AliExpress may be a product brand, not the seller",
}

# On the B2B marketplaces the listing's "brand" is the factory that makes it, so
# Zyte's brand field is the supplier. AliExpress is a consumer marketplace where
# brand is genuinely the product's brand and frequently is NOT the store, so a
# name from there would be a plausible-looking wrong answer — the one thing this
# codebase refuses to produce. Take the name only where it means what we need.
BRAND_IS_SUPPLIER = {"alibaba", "1688", "made_in_china"}

# One page fetch per listing at ~38s. Capped hard, and spent on the rows that
# already survived visual matching.
RESOLVE_TOP_N = 12
RESOLVE_CONCURRENCY = 6
# The combined product+browserHtml call is heavier than either alone.
RESOLVE_TIMEOUT = 180.0

MOQ_KEYS = {"moq", "min order", "minimum order", "min. order", "minimum order quantity"}
ORIGIN_KEYS = {"place of origin", "origin", "made in"}

# Deliberately NOT taken from the product page: verification badges and years
# active. Both phrases ("Verified Supplier", "Gold Supplier", "18 yrs") appear
# dozens of times in Alibaba's own page chrome, adverts and footer — a live
# probe found 23 badge hits and years of 1, 18 and 63 on a single page, none of
# them attributable to this seller. A badge is a trust claim, and a wrongly
# attributed one is worse than an absent one, so those stay None until they can
# come from the seller's own page.


# Alibaba ships a complete supplier record in the product page's own JSON, and
# that page — unlike the company minisite — is not bot-checked. Probed live:
#
#   companyName, companyBusinessType, companyJoinYears, companyId,
#   companyProfileUrl, contactName ("Ms. zhao"), companyRegisterCountry
#
# Two neighbouring fields explain why no email or phone appears anywhere on
# these sites: `contactEncryptId` is the contact identity, served *encrypted*,
# and `supplierOperationalAddress` comes back as the literal placeholder
# "INTL_ONSITE". The details are not on a page we failed to find — they are
# withheld from anonymous visitors by design. So this mines what is served and
# the scan in supplier_profile reports the absence honestly.
#
# The JSON arrives inside an HTML attribute, so quotes may be backslash-escaped.
def _json_field(html: str, key: str) -> str | None:
    match = re.search(rf'\\?"{key}\\?"\s*:\s*\\?"(.*?)\\?"', html)
    if not match:
        return None
    value = match.group(1).replace("\\/", "/").replace('\\"', '"').strip()
    return value or None


# The product page ships both the real record and the i18n templates for the
# enquiry form, and both contain a `contactName` key — one live listing came
# back with the literal placeholder "name" as its contact person. A placeholder
# rendered as a supplier contact is a wrong answer, not a thin one.
CONTACT_PLACEHOLDERS = {"name", "contactname", "contact name", "contact", "n/a", "-", "null", "none"}


def _clean_contact_name(value: str | None) -> str | None:
    if not value:
        return None
    stripped = value.strip()
    if len(stripped) < 2 or stripped.lower() in CONTACT_PLACEHOLDERS:
        return None
    return stripped[:80]


def _profile_from_embedded_json(product: Product, html: str) -> SupplierProfile | None:
    """The supplier record the product page carries about its own seller."""
    name = _json_field(html, "companyName")
    if not name:
        return None

    years = None
    raw_years = _json_field(html, "companyJoinYears")
    if raw_years and raw_years.isdigit() and 1 <= int(raw_years) <= 60:
        years = int(raw_years)

    business = _json_field(html, "companyBusinessType")
    country = _json_field(html, "companyRegisterCountry")
    profile_url = _json_field(html, "companyProfileUrl")
    contact_name = _clean_contact_name(_json_field(html, "contactName"))

    # Trade Assurance is a per-supplier flag in this same record, unlike the
    # badge phrases scattered through the page's marketing chrome — so it is
    # the one verification signal that can be attributed to this company.
    verified = True if re.search(r'\\?"baoDisplayAssurance\\?"\s*:\s*true', html) else None

    return SupplierProfile(
        site=product.site,
        supplier_url=profile_url or product.seller_url or product.product_url,
        company_name=name,
        location=country,
        years_active=years,
        business_type=business.lower() if business else None,
        verified=verified,
        contact_name=contact_name,
    )


def _profile_from_product_page(product: Product, data: dict) -> SupplierProfile | None:
    """The supplier facts the *product* page can honestly support.

    The company's own minisite is behind a bot check on every Alibaba supplier
    tested — through plain Zyte and through a proxied cloud browser alike — so
    contact details are genuinely unavailable, not merely unfetched. What the
    product page does carry, structurally and unchallenged, is who makes it and
    where. Reporting that beats reporting nothing, provided it doesn't pretend
    to be the full profile: emails/phones stay empty and the warning says why.
    """
    name = _brand_name(data, product.site)
    origin = None
    for prop in data.get("additionalProperties") or []:
        if isinstance(prop, dict) and str(prop.get("name", "")).strip().lower() in ORIGIN_KEYS:
            origin = str(prop.get("value") or "")[:120] or None
            break
    if not name and not origin:
        return None
    return SupplierProfile(
        site=product.site,
        supplier_url=product.seller_url or product.product_url,
        company_name=name,
        location=origin,
        warning=(
            "Company name and location come from the product page; this supplier's own "
            "page is behind a bot check, so published contact details could not be read. "
            "Use the marketplace enquiry form."
        ),
    )


def _company_url(html: str, site: str) -> str | None:
    pattern = COMPANY_URL_PATTERNS.get(site)
    if not pattern or not html:
        return None
    match = pattern.search(html)
    return match.group(0) if match else None


def _moq(product_data: dict) -> str | None:
    for prop in product_data.get("additionalProperties") or []:
        if not isinstance(prop, dict):
            continue
        name = str(prop.get("name", "")).strip().lower()
        if name in MOQ_KEYS and prop.get("value"):
            return str(prop["value"])[:60]
    return None


def _brand_name(product_data: dict, site: str) -> str | None:
    if site not in BRAND_IS_SUPPLIER:
        return None
    brand = product_data.get("brand")
    if isinstance(brand, dict):
        name = (brand.get("name") or "").strip()
        return name or None
    return None


async def _resolve_one(
    product: Product, zyte: ZyteClient, semaphore: asyncio.Semaphore
) -> tuple[str | None, SupplierProfile | None]:
    """Fill seller_name / seller_url / moq on one listing from its own page.

    Returns (warning, fallback_profile). The warning is set only when the page
    could not be read; fields the page doesn't publish stay None — an absent
    supplier is reported as absent, never guessed at from the product title.
    """
    async with semaphore:
        try:
            payload = await zyte.extract_with_product(product.product_url, timeout=RESOLVE_TIMEOUT)
        except ZyteError as e:
            return f"[{product.site}] could not open a product page to find its supplier: {e}", None
        except Exception as e:  # noqa: BLE001 - one dead page must not sink the batch
            return f"[{product.site}] product page error: {e}", None

    data = payload.get("product") or {}
    html = payload.get("browserHtml") or ""

    if not product.seller_name:
        product.seller_name = _brand_name(data, product.site)
    if not product.seller_url:
        product.seller_url = _company_url(html, product.site)
    if not product.moq:
        product.moq = _moq(data)
    # Every supplier page on these sites is an enquiry form, not an inbox. Say
    # so only once there is actually a page to send the enquiry through.
    if product.seller_url and not product.contact_type:
        product.contact_type = "form"
        product.contact_value = product.seller_url

    # The embedded record is richer than anything Zyte's generic extraction
    # exposes (business type, years on the platform, the named contact), so it
    # leads; the structured fields fill the gaps on sites that don't ship it.
    profile = _profile_from_embedded_json(product, html) or _profile_from_product_page(product, data)
    if profile and not profile.company_name:
        profile.company_name = _brand_name(data, product.site)
    if profile and not product.seller_name and profile.company_name:
        product.seller_name = profile.company_name
    return None, profile


async def resolve(
    products: list[Product], zyte: ZyteClient
) -> tuple[list[str], dict[str, SupplierProfile]]:
    """Fill in the supplier behind each listing, in place.

    Returns (warnings, fallback profiles keyed by product_url). The profiles are
    what the product page alone can support, for use when the supplier's own
    page can't be read — which on Alibaba is every time.

    Only listings that are actually missing a seller are fetched — sites whose
    own parser already supplied one (Made-in-China's search results carry the
    company name) cost nothing here.
    """
    targets = [
        p
        for p in products[:RESOLVE_TOP_N]
        if not p.seller_url and not p.seller_name and p.product_url
    ]
    if not targets:
        return [], {}

    semaphore = asyncio.Semaphore(RESOLVE_CONCURRENCY)
    outcomes = await asyncio.gather(
        *(_resolve_one(p, zyte, semaphore) for p in targets), return_exceptions=True
    )

    warnings: list[str] = []
    profiles: dict[str, SupplierProfile] = {}
    failed = 0
    for product, outcome in zip(targets, outcomes):
        if isinstance(outcome, BaseException):
            failed += 1
            continue
        warning, profile = outcome
        if warning:
            failed += 1
            if len(warnings) < 3:  # one example is diagnostic; twelve is noise
                warnings.append(warning)
        if profile:
            profiles[product.product_url] = profile

    named = sum(1 for p in targets if p.seller_name)
    linked = sum(1 for p in targets if p.seller_url)

    if named or linked:
        warnings.append(
            f"Identified the supplier behind {named} of {len(targets)} listing(s) by "
            f"opening the product page; {linked} link to a company page."
        )
    if failed:
        warnings.append(
            f"{failed} product page(s) could not be opened, so those listings show no supplier."
        )
    if targets and not named and not linked:
        warnings.append(
            "None of these listings published a supplier on the product page — the "
            "products are real, the company behind them isn't shown."
        )
    return warnings, profiles
