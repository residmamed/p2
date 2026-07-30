"""Read a supplier's own site for a way to contact them — text and pictures.

The counterpart to `app/supplier_profile.py`, and it exists because of that
module's central measured finding: scanning four pages of each Alibaba
supplier's minisite returned *no* email, phone or WhatsApp for any of them. The
product page's own JSON explains why — `contactEncryptId` is served encrypted
and `supplierOperationalAddress` is the literal placeholder `INTL_ONSITE`. The
platform withholds contact details from anonymous visitors by design.

That finding is about the **text**. It was never about the whole page.

Suppliers who want to be reachable outside the marketplace's enquiry form put
their address and number where the platform's stripping and the scraper's regex
both fail to look: inside a banner graphic, a business-card image, a certificate
scan, the artwork on a "Contact Us" tab. A JPEG survives both. So this module
fetches the pages, hands Claude the text *and* the images, and asks it to
transcribe what is visibly there (`claude_agent.read_supplier_contacts`).

**Why Oxylabs rather than Zyte here.** supplier_profile measured Alibaba
minisites as bot-checked through Zyte on every attempt, plain fetch and proxied
browser alike. This pipeline already holds an Oxylabs session that reaches
Alibaba product pages unchallenged, so it is the transport with something new to
try. Where it is also challenged, that is reported as challenged — a bot check
is never parsed for contacts, for the same reason supplier_profile refuses to:
a challenge page has a title, a copyright range and boilerplate links, and
mining it produces a company name and a phone number belonging to the bot check.

**Cost, and why this is opt-in.** Each supplier costs up to
MAX_PAGES_PER_SUPPLIER page fetches, up to CONTACT_MAX_IMAGES image downloads
and one vision call. That is seconds, not milliseconds, against a pipeline whose
whole purpose is answering in under five. So `find_suppliers` runs it only when
the request asks (`include_contacts`), and only for the top few suppliers.
"""
import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from . import claude_agent
from .config import settings
from .models import SupplierContacts
from .oxylabs_client import OxylabsClient, OxylabsError
from .supplier_profile import (
    EMAIL_DENYLIST,
    MAX_CONTACTS_PER_KIND,
    is_challenged,
)

from parsel import Selector

# Suppliers to look up per search. Each is several fetches plus a vision call,
# and the user contacts one or two, not twelve.
CONTACT_TOP_N = 5
CONTACT_CONCURRENCY = 3
# Pages of one company's site. Contact details sit on a contact page far more
# often than on whatever the listing happened to link to.
MAX_PAGES_PER_SUPPLIER = 3
# Rendered, so slower than the product-page fetches in oxylabs_client. Measured
# on a live Alibaba minisite: the raw HTML carries 0 <img> tags and 2 image URLs
# because the page builds itself client-side; rendering it yields 21 tags and 24
# URLs. Without rendering there is simply nothing for the vision pass to read,
# which would make this whole module a text scan with extra steps.
PAGE_TIMEOUT = 30.0
IMAGE_TIMEOUT = 8.0
IMAGE_CONCURRENCY = 6

# Same paths supplier_profile walks, trimmed to the ones that actually carry
# contacts — this runs a heavier per-page cost so the budget is spent narrower.
COMPANY_PAGE_PATHS: dict[str, tuple[str, ...]] = {
    "alibaba": ("contactinfo.html", "company_profile.html"),
    "1688": ("page/contactinfo.htm",),
    "taobao": (),
}

# Images worth spending a vision call on. Contact details live in banners,
# headers, "contact us" artwork and business cards — not in the product
# thumbnails that make up most of a minisite's images.
PROMISING_IMAGE_RE = re.compile(
    r"banner|contact|header|about|company|profile|cert|licen|card|info|footer|top", re.I
)
# Sprites, icons, spacers and tracking pixels. Nothing under this is readable
# text, and each one wastes a slot a banner could have used.
MIN_IMAGE_BYTES = 8 * 1024
SKIP_IMAGE_RE = re.compile(r"\.svg($|\?)|sprite|icon|logo_?small|blank|pixel|spacer|1x1", re.I)

# Alibaba's CDN puts the pixel dimensions in the filename — `tps-920-110` for a
# banner, `tps-84-84` for a UI icon, `TB1W1Qc...-382-80.png` for a masthead. Free
# triage: a small square is chrome, a wide strip is the banner that might have a
# phone number across it. Reading it costs nothing and saves a download plus a
# slot in the vision request.
IMAGE_DIMS_RE = re.compile(r"[-_](\d{2,5})[-_](\d{2,5})\.(?:jpg|jpeg|png|webp)", re.I)
MIN_INTERESTING_EDGE = 120

FETCH_HEADERS = claude_agent.FETCH_HEADERS

# A phone number read out of a graphic still has to look like a phone number.
# supplier_profile learned this the expensive way: unanchored digit matching on
# these pages returned a screen resolution (1920-1200) and an internal id
# (429-256778) as supplier phone numbers. The agent is told the same rules, but
# the check is repeated here because a prompt is guidance and this is a gate.
YEAR_RANGE_RE = re.compile(r"^(19|20)\d{2}\s*[-–—]\s*(19|20)\d{2}$")
EMAIL_SHAPE_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass
class _Page:
    url: str
    text: str = ""
    image_urls: list[str] = field(default_factory=list)
    challenged: bool = False


def is_configured() -> bool:
    return claude_agent.is_configured()


def company_page_urls(url: str, site: str) -> list[str]:
    """This company's pages worth reading, most likely first."""
    match = re.match(r"(https?://[^/]+)", url)
    if not match:
        return [url]
    base = match.group(1)
    urls = [url]
    for path in COMPANY_PAGE_PATHS.get(site, ()):
        candidate = f"{base}/{path}"
        if candidate not in urls:
            urls.append(candidate)
    return urls[:MAX_PAGES_PER_SUPPLIER]


def _extract(html: str, url: str) -> _Page:
    """Visible text and candidate image URLs from one page."""
    if is_challenged(html):
        return _Page(url=url, challenged=True)

    selector = Selector(text=html)
    text = " ".join(selector.xpath("//body//text()").getall())
    text = re.sub(r"\s+", " ", text).strip()[:40_000]

    images: list[str] = []
    for attribute in ("src", "data-src", "data-lazy-src", "data-original"):
        for raw in selector.css(f"img::attr({attribute})").getall():
            if not raw or SKIP_IMAGE_RE.search(raw):
                continue
            absolute = urljoin(url, raw if not raw.startswith("//") else "https:" + raw)
            if absolute.startswith("http") and not _is_chrome(absolute) and absolute not in images:
                images.append(absolute)

    # Best candidates first, so the CONTACT_MAX_IMAGES budget goes to banners
    # rather than to whichever product thumbnail happened to be first in the DOM.
    images.sort(key=_image_rank)
    return _Page(url=url, text=text, image_urls=images[:20])


def _dimensions(url: str) -> tuple[int, int] | None:
    match = IMAGE_DIMS_RE.search(url)
    if not match:
        return None
    try:
        return int(match.group(1)), int(match.group(2))
    except ValueError:
        return None


def _is_chrome(url: str) -> bool:
    """A small square in the URL's own filename is a UI icon, not a banner."""
    dims = _dimensions(url)
    if dims is None:
        return False
    width, height = dims
    return width < MIN_INTERESTING_EDGE and height < MIN_INTERESTING_EDGE


def _image_rank(url: str) -> tuple[int, int]:
    """Named-like-a-banner first, then widest. Contact details are laid across
    a wide graphic far more often than down a square product shot."""
    named = 0 if PROMISING_IMAGE_RE.search(url) else 1
    dims = _dimensions(url)
    return (named, -(dims[0] if dims else 0))


def _page_client() -> OxylabsClient:
    """A *rendering* client, unlike the one enrichment uses.

    Product pages serve everything we read in their initial HTML, so step 2
    leaves rendering off and stays fast. A company minisite does the opposite —
    it builds itself client-side, and unrendered it hands back a page with no
    <img> tags at all. Rendering is the difference between a vision pass and no
    vision pass, which is the whole point of this module.
    """
    return OxylabsClient(timeout=PAGE_TIMEOUT, render=True)


async def _fetch_pages(url: str, site: str, oxylabs: OxylabsClient) -> tuple[list[_Page], int]:
    """Fetch this company's pages concurrently. Returns (readable, challenged)."""
    urls = company_page_urls(url, site)
    outcomes = await asyncio.gather(
        *(oxylabs.scrape(u, site) for u in urls), return_exceptions=True
    )

    pages: list[_Page] = []
    challenged = 0
    for page_url, outcome in zip(urls, outcomes):
        if isinstance(outcome, (BaseException, OxylabsError)) or not isinstance(outcome, str):
            continue
        page = _extract(outcome, page_url)
        if page.challenged:
            challenged += 1
            continue
        if page.text or page.image_urls:
            pages.append(page)
    return pages, challenged


async def _fetch_images(urls: list[str]) -> list[bytes]:
    """Download the candidate graphics. Tiny files are dropped after the fact —
    an icon's dimensions aren't in its URL, so the size check needs the bytes."""
    semaphore = asyncio.Semaphore(IMAGE_CONCURRENCY)

    async def _one(client: httpx.AsyncClient, image_url: str) -> bytes | None:
        async with semaphore:
            try:
                response = await client.get(
                    image_url, timeout=IMAGE_TIMEOUT, follow_redirects=True, headers=FETCH_HEADERS
                )
                response.raise_for_status()
            except httpx.HTTPError:
                return None
        content = response.content
        return content if len(content) >= MIN_IMAGE_BYTES else None

    async with httpx.AsyncClient() as client:
        raw = await asyncio.gather(*(_one(client, u) for u in urls), return_exceptions=True)
    return [r for r in raw if isinstance(r, bytes)]


def _keep_email(value: str) -> bool:
    return bool(EMAIL_SHAPE_RE.match(value)) and not EMAIL_DENYLIST.search(value)


def _keep_phone(value: str) -> bool:
    """A digit string is a phone number only if it could be one. See the
    module's YEAR_RANGE_RE note — this gate is not redundant with the prompt."""
    if YEAR_RANGE_RE.match(value.strip()):
        return False
    digits = re.sub(r"\D", "", value)
    # 8 is the shortest real international subscriber number; 15 is the E.164
    # maximum, above which it is an id that happens to be made of digits.
    return 8 <= len(digits) <= 15


def _collect(
    pairs: list[tuple[str, str]], keep, found_in: dict[str, str], kind: str
) -> list[str]:
    """Dedupe, gate, and record whether each channel came from text or a picture."""
    values: list[str] = []
    sources: set[str] = set()
    for value, source in pairs:
        cleaned = value.strip()
        if not cleaned or cleaned in values or not keep(cleaned):
            continue
        values.append(cleaned)
        sources.add("image" if source == "image" else "text")
    if values:
        found_in[kind] = "both" if len(sources) > 1 else next(iter(sources))
    return values[:MAX_CONTACTS_PER_KIND]


async def read_contacts(
    supplier_url: str,
    site: str,
    company_name: str | None = None,
    oxylabs: OxylabsClient | None = None,
) -> SupplierContacts:
    """Scan one supplier's site for a way to reach them. Never raises."""
    contacts = SupplierContacts()
    if not supplier_url:
        contacts.warning = "This listing didn't link a company page, so there was nothing to scan."
        return contacts
    if not claude_agent.is_configured():
        contacts.warning = (
            "Contact reading needs Claude — set ANTHROPIC_API_KEY in backend/.env."
        )
        return contacts

    # Deliberately ignores any client passed in: enrichment's is tuned for
    # speed with rendering off, and these pages are empty without it.
    try:
        pages, challenged = await _fetch_pages(supplier_url, site, _page_client())
    except Exception as e:  # noqa: BLE001 - one supplier must not sink the batch
        contacts.warning = f"Could not open this supplier's pages: {e}"
        return contacts

    if not pages:
        contacts.warning = (
            "This supplier's own pages returned a bot check, so nothing could be read."
            if challenged
            else "This supplier's own pages could not be opened."
        )
        return contacts

    image_urls: list[str] = []
    for page in pages:
        for candidate in page.image_urls:
            if candidate not in image_urls:
                image_urls.append(candidate)
    images = await _fetch_images(image_urls[: claude_agent.CONTACT_MAX_IMAGES * 2])

    findings, warnings = await claude_agent.read_supplier_contacts(
        company_name or "", [p.text for p in pages if p.text], images
    )
    contacts.pages_scanned = len(pages)
    contacts.images_read = min(len(images), claude_agent.CONTACT_MAX_IMAGES)

    if findings is None:
        # Never "publishes nothing" — the agent didn't answer, which is a
        # different fact and the user acts on it differently.
        contacts.warning = warnings[0] if warnings else "The contact reader did not answer."
        return contacts

    found_in: dict[str, str] = {}
    contacts.emails = _collect(findings.emails, _keep_email, found_in, "emails")
    contacts.phones = _collect(findings.phones, _keep_phone, found_in, "phones")
    contacts.whatsapp = _collect(findings.whatsapp, _keep_phone, found_in, "whatsapp")
    contacts.wechat = _collect(findings.wechat, lambda v: len(v) >= 3, found_in, "wechat")
    contacts.contact_name = findings.contact_name
    contacts.found_in = found_in

    if contacts.is_empty():
        detail = f"read {contacts.pages_scanned} page(s)"
        if contacts.images_read:
            detail += f" and {contacts.images_read} image(s)"
        contacts.warning = (
            f"{detail} of this supplier's site — no email, phone, WeChat or WhatsApp is "
            "published, in the text or in the pictures. Contact goes through the "
            "marketplace enquiry form."
        )
    return contacts


async def enrich_matches(matches: list, oxylabs: OxylabsClient | None = None) -> list[str]:
    """Fill `contacts` on the top few matches that link a company page, in place.

    Returns warnings. Rows past the cap keep `contacts=None`, which the schema
    defines as "not looked for" rather than "nothing published".
    """
    if not settings.claude_supplier_contacts:
        return []
    if not claude_agent.is_configured():
        return [
            "Supplier contact reading is on but Claude is not configured — set "
            "ANTHROPIC_API_KEY in backend/.env."
        ]

    targets = [m for m in matches if getattr(m, "supplier_url", None)][:CONTACT_TOP_N]
    if not targets:
        return [
            "No supplier linked a company page, so there was nothing to scan for "
            "contact details."
        ]

    semaphore = asyncio.Semaphore(CONTACT_CONCURRENCY)

    async def _one(match):
        async with semaphore:
            return await read_contacts(
                match.supplier_url, match.source, match.supplier_name, oxylabs
            )

    results = await asyncio.gather(*(_one(m) for m in targets), return_exceptions=True)

    reachable = 0
    from_images = 0
    for match, result in zip(targets, results):
        if isinstance(result, BaseException):
            match.contacts = SupplierContacts(warning=f"Contact scan failed: {result}")
            continue
        match.contacts = result
        if not result.is_empty():
            reachable += 1
            if "image" in result.found_in.values() or "both" in result.found_in.values():
                from_images += 1

    warnings: list[str] = []
    if reachable:
        note = (
            f"Found direct contact details for {reachable} of {len(targets)} supplier(s) "
            "scanned"
        )
        if from_images:
            note += f" — {from_images} of them only by reading the pictures on their pages"
        warnings.append(note + ".")
    else:
        warnings.append(
            f"Scanned {len(targets)} supplier site(s), text and images: none publishes a "
            "direct email or phone. On these marketplaces that is the norm rather than a "
            "scan failure — contact runs through the enquiry form on each listing."
        )
    return warnings
