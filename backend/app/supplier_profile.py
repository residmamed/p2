"""Stage 3: given a supplier's page URL, pull the company-level facts off it
with Zyte.

Deliberately generic rather than per-site. Zyte's automatic extraction covers
products, not companies, so there's nothing to lean on — but the fields worth
having (company name, location, years active, whether an email/phone is
published at all) show up in broadly the same shapes across Alibaba, 1688 and
Made-in-China: meta tags, `mailto:`/`tel:`/`wa.me` hrefs, and a small set of
recurring label phrases in both English and Chinese.

Regex + meta tags over three hand-written company parsers is the right trade
here: the value is in *whether a contact channel is published at all*, which is
robust to markup changes, and a missing field is honestly reported as missing.
The README's finding still holds — `contact_type` is "form" almost everywhere,
so treat any direct hit as a bonus, not an expectation.
"""
import asyncio
import base64
import re

from parsel import Selector

from .models import SupplierProfile
from .zyte_client import ZyteClient

# These marketplaces answer a plain fetch of a company minisite with a bot
# check often enough that it has to be a first-class outcome. Measured on a live
# Alibaba run: all four supplier pages came back challenged, and because the
# challenge page has a <title>, _company_name happily returned "Captcha
# Interception" as the company — a confident-looking wrong answer, and one that
# also short-circuited the cheap->browser escalation below, since a non-empty
# name reads as success. Detect it, report it, and keep every field empty.
CHALLENGE_MARKERS = (
    "captcha interception",
    "滑动验证",
    "please slide to verify",
    "verify to continue",
    "unusual traffic",
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A copyright range ("1999-2026") is eight digits with a separator and sails
# through the phone regex — every challenged page above reported it as the
# supplier's phone number. Two 4-digit groups that both look like years are
# never a phone number.
YEAR_RANGE_RE = re.compile(r"^(19|20)\d{2}\s*[-–—]\s*(19|20)\d{2}$")
WHATSAPP_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\+?\d{6,15})")
# A bare digit-shape match over a marketplace page is not a phone number
# detector. Run unanchored against live supplier pages it returned "1920-1200"
# (a screen resolution out of the page's CSS) and "429-256778" (an internal id)
# and presented both as the supplier's phone. So free text is only mined
# immediately after an explicit phone label — if the page doesn't say it's a
# phone number, we don't claim it is one.
PHONE_LABEL_RE = re.compile(
    r"(?:tel|telephone|phone|mobile|cell|fax|whats\s?app|电话|手机|传真)"
    r"\s*(?:no\.?|number)?\s*[:：]\s*"
    r"(\+?\d[\d\s.\-()]{6,22}\d)",
    re.I,
)

YEARS_RE = re.compile(r"(\d{1,2})\s*(?:yrs?|years?)\b", re.I)
# 1688/Alibaba Chinese equivalents: "第5年" / "5年"
YEARS_CN_RE = re.compile(r"第?\s*(\d{1,2})\s*年")

BUSINESS_TYPES = [
    ("manufacturer", re.compile(r"manufacturer|factory|生产厂家|工厂", re.I)),
    ("trading company", re.compile(r"trading compan|trader|贸易公司", re.I)),
    ("supplier", re.compile(r"\bsupplier\b|供应商", re.I)),
]
VERIFIED_RE = re.compile(
    r"verified supplier|gold supplier|trade assurance|audited supplier|诚信通|实力商家", re.I
)

# Boilerplate that appears in mailto: links on every page of these sites and is
# never the supplier's own address.
EMAIL_DENYLIST = re.compile(
    r"@(?:alibaba|aliexpress|1688|made-in-china|alicdn|taobao)\.(?:com|cn)$|"
    r"^(?:service|support|abuse|noreply|no-reply|privacy|legal)@",
    re.I,
)

ENRICH_CONCURRENCY = 6
MAX_CONTACTS_PER_KIND = 3

# A supplier's contact details are rarely on the page you first land on. Where
# they are published at all they sit on a dedicated contact page, and the
# "company profile" page is often just a marketing blurb. So the scan walks the
# company's site rather than a single URL.
#
# Paths are relative to the company's own host and are tried in likelihood
# order; every one that loads is mined and the findings are merged, because
# different pages publish different channels (an address on About, a phone on
# Contact). Ones that 404 cost a fetch and are skipped silently — these sites
# do not use consistent URLs across accounts.
COMPANY_PAGE_PATHS: dict[str, tuple[str, ...]] = {
    "alibaba": ("contactinfo.html", "company_profile.html", "aboutus.html", "index.html"),
    "made_in_china": ("contact.html", "aboutus.html", "index.html"),
    "1688": ("page/contactinfo.htm", "page/creditdetail.htm", ""),
    "aliexpress": ("",),
}
DEFAULT_COMPANY_PAGES = ("contact.html", "contactinfo.html", "aboutus.html", "index.html", "")
# Each extra page is another fetch per supplier, and enrichment already runs on
# up to 12 suppliers per search.
MAX_PAGES_PER_COMPANY = 4


def _text(sel: Selector) -> str:
    return " ".join(sel.xpath("//body//text()").getall())[:200_000]


# A company minisite's <title> and og:site_name are frequently the marketplace's
# own name, not the company's — live scans returned "Alibaba.com" as the
# supplier for every listing, overwriting the real company name the product page
# had already supplied. A generic title is no name at all.
GENERIC_NAME_RE = re.compile(
    r"^\s*(?:www\.)?(?:alibaba|aliexpress|1688|made[\s\-]?in[\s\-]?china|taobao|tmall)"
    r"(?:\.com|\.cn)?\s*$|^\s*(?:home|index|company\s*profile|contact\s*us|about\s*us|"
    r"supplier|manufacturer|captcha\s*interception)\s*$",
    re.I,
)


def _company_name(sel: Selector, fallback_url: str) -> str | None:
    for path in (
        "//meta[@property='og:site_name']/@content",
        "//meta[@property='og:title']/@content",
        "//h1//text()",
        "//title/text()",
    ):
        value = sel.xpath(path).get()
        if value and value.strip() and not GENERIC_NAME_RE.match(value.strip()):
            return value.strip()[:200]
    return None


def _location(sel: Selector, body: str) -> str | None:
    for path in (
        "//meta[@name='location']/@content",
        "//*[contains(@class,'location')]//text()",
        "//*[contains(@class,'address')]//text()",
    ):
        value = sel.xpath(path).get()
        if value and value.strip():
            return value.strip()[:120]
    match = re.search(r"(?:Location|Based in|所在地)\s*[:：]\s*([^\n|]{3,80})", body)
    return match.group(1).strip() if match else None


def _years(body: str) -> int | None:
    for pattern in (YEARS_RE, YEARS_CN_RE):
        match = pattern.search(body)
        if match:
            years = int(match.group(1))
            if 1 <= years <= 60:
                return years
    return None


def _business_type(body: str) -> str | None:
    for label, pattern in BUSINESS_TYPES:
        if pattern.search(body):
            return label
    return None


def _contacts(sel: Selector, body: str) -> tuple[list[str], list[str], list[str]]:
    hrefs = sel.xpath("//a/@href").getall()

    emails = {
        h.split("mailto:", 1)[1].split("?")[0].strip()
        for h in hrefs
        if h.lower().startswith("mailto:")
    }
    emails |= set(EMAIL_RE.findall(body))
    emails = {e for e in emails if not EMAIL_DENYLIST.search(e)}

    whatsapp = set()
    for h in hrefs:
        match = WHATSAPP_RE.search(h)
        if match:
            whatsapp.add(match.group(1))

    phones = {
        h.split("tel:", 1)[1].split("?")[0].strip()
        for h in hrefs
        if h.lower().startswith("tel:")
    }
    # Only mine free text for phones when no tel: link exists — a labelled
    # match is still weaker evidence than a link the site itself marked as a
    # phone number.
    if not phones:
        phones = set()
        for candidate in PHONE_LABEL_RE.findall(body):
            value = candidate.strip().strip(".,;-")
            digits = re.sub(r"\D", "", value)
            # 8 is the shortest real international subscriber number; 15 is the
            # E.164 maximum, above which it's an id that happens to have digits.
            if 8 <= len(digits) <= 15 and not YEAR_RANGE_RE.match(value):
                phones.add(value)

    return (
        sorted(emails)[:MAX_CONTACTS_PER_KIND],
        sorted(phones)[:MAX_CONTACTS_PER_KIND],
        sorted(whatsapp)[:MAX_CONTACTS_PER_KIND],
    )


def is_challenged(html: str) -> bool:
    """True when the page is a bot check rather than the supplier's page.

    Checked against the title and the first slice of body text only: the phrase
    "unusual traffic" could plausibly appear in a company's own copy further
    down, and a false positive here silently discards a real profile.
    """
    head = html[:4000].lower()
    sel = Selector(text=html)
    title = (sel.xpath("//title/text()").get() or "").lower()
    return any(m in title or m in head for m in CHALLENGE_MARKERS)


def parse_supplier_page(html: str, url: str, site: str) -> SupplierProfile:
    """Pure function over page HTML — unit-testable against saved fixtures the
    same way the product parsers are."""
    if is_challenged(html):
        # Deliberately every field empty. A challenge page has a title, a
        # copyright range and boilerplate links; parsing it produces a company
        # name, a phone number and a verified badge that belong to the bot
        # check, not to any supplier.
        return SupplierProfile(
            site=site,
            supplier_url=url,
            warning="the supplier's company page returned a bot check, so no company details could be read",
        )

    sel = Selector(text=html)
    body = _text(sel)
    emails, phones, whatsapp = _contacts(sel, body)
    return SupplierProfile(
        site=site,
        supplier_url=url,
        company_name=_company_name(sel, url),
        location=_location(sel, body),
        years_active=_years(body),
        business_type=_business_type(body),
        verified=True if VERIFIED_RE.search(body) else None,
        emails=emails,
        phones=phones,
        whatsapp=whatsapp,
    )


def merge_profiles(into: SupplierProfile, other: SupplierProfile) -> SupplierProfile:
    """Fold one page's findings into the profile built so far.

    First non-empty value wins for scalars, because pages are visited in
    likelihood order — the contact page's phone beats the homepage footer's.
    Contact lists are unioned instead: a supplier that publishes an email on
    About and a WhatsApp on Contact has both, and picking one page's answer
    would discard the other.
    """
    for field in ("company_name", "location", "years_active", "business_type", "verified", "contact_name"):
        if getattr(into, field) in (None, "") and getattr(other, field) not in (None, ""):
            setattr(into, field, getattr(other, field))
    for field in ("emails", "phones", "whatsapp"):
        merged = list(dict.fromkeys([*getattr(into, field), *getattr(other, field)]))
        setattr(into, field, merged[:MAX_CONTACTS_PER_KIND])
    return into


def company_page_urls(url: str, site: str) -> list[str]:
    """The pages of this company's own site worth reading, most likely first.

    Built from the company's host rather than by crawling links, because the
    minisite navigation is JS-rendered and a link crawl would need the very
    browser mode these sites challenge.
    """
    match = re.match(r"(https?://[^/]+)", url)
    if not match:
        return [url]
    base = match.group(1)
    paths = COMPANY_PAGE_PATHS.get(site, DEFAULT_COMPANY_PAGES)
    urls = [url]  # whatever the listing linked to stays first
    for path in paths:
        candidate = f"{base}/{path}" if path else f"{base}/"
        if candidate not in urls:
            urls.append(candidate)
    return urls[:MAX_PAGES_PER_COMPANY]


async def _fetch_page(url: str, site: str, zyte: ZyteClient) -> SupplierProfile | None:
    """One page, cheap mode first then browser. None when nothing was readable.

    The cost ladder is the same one the Made-in-China scraper uses, but the
    order matters more here than it looks: measured against live Alibaba
    minisites, the cheap httpResponseBody mode returns the page while browser
    mode is challenged on every attempt — the opposite of the usual assumption
    that rendering gets you further.
    """
    challenged = False
    for browser in (False, True):
        try:
            result = await zyte.extract(
                url, browser_html=browser, http_response_body=not browser, max_retries=1
            )
        except Exception:  # noqa: BLE001 - one page of several; keep going
            continue

        if browser:
            html = result.get("browserHtml", "")
        else:
            body_b64 = result.get("httpResponseBody", "")
            html = base64.b64decode(body_b64).decode("utf-8", errors="replace") if body_b64 else ""
        if not html:
            continue

        profile = parse_supplier_page(html, url, site)
        if profile.warning and is_challenged(html):
            challenged = True
            continue  # try the other mode
        return profile

    return SupplierProfile(site=site, supplier_url=url, warning="challenged") if challenged else None


async def fetch_supplier_profile(url: str, site: str, zyte: ZyteClient) -> SupplierProfile:
    """Scan the supplier's own site and return everything it publishes.

    Several pages, not one: contact details live on a contact page far more
    often than on the page a product listing happens to link to, and different
    pages publish different channels. Pages are fetched concurrently and their
    findings merged.

    Never raises: a failed enrichment downgrades one row, it doesn't sink the
    batch.
    """
    urls = company_page_urls(url, site)
    results = await asyncio.gather(
        *(_fetch_page(u, site, zyte) for u in urls), return_exceptions=True
    )

    profile = SupplierProfile(site=site, supplier_url=url)
    read = 0
    challenged = 0
    for outcome in results:
        if isinstance(outcome, BaseException) or outcome is None:
            continue
        if outcome.warning == "challenged":
            challenged += 1
            continue
        read += 1
        merge_profiles(profile, outcome)

    profile.pages_scanned = read
    profile.supplier_url = url

    if read == 0:
        profile.warning = (
            "the supplier's company pages returned a bot check, so no company details "
            "could be read"
            if challenged
            else "no company details found on this supplier's pages"
        )
    elif not (profile.emails or profile.phones or profile.whatsapp):
        # Say which of the two it is. "We looked and they publish none" and "we
        # could not look" are different facts and the user acts on them
        # differently.
        profile.warning = (
            f"scanned {read} page(s) of this supplier's site; no email, phone or WhatsApp "
            "is published publicly — contact goes through the marketplace enquiry form"
        )
    return profile


async def enrich(urls_with_site: list[tuple[str, str]], zyte: ZyteClient) -> dict[str, SupplierProfile]:
    """Fetch profiles for a batch of (url, site) pairs, deduped, concurrency-capped."""
    unique: dict[str, str] = {}
    for url, site in urls_with_site:
        if url and url not in unique:
            unique[url] = site

    sem = asyncio.Semaphore(ENRICH_CONCURRENCY)

    async def _one(url: str, site: str) -> tuple[str, SupplierProfile]:
        async with sem:
            return url, await fetch_supplier_profile(url, site, zyte)

    pairs = await asyncio.gather(
        *(_one(u, s) for u, s in unique.items()), return_exceptions=True
    )
    out: dict[str, SupplierProfile] = {}
    for pair in pairs:
        if isinstance(pair, Exception):
            continue
        url, profile = pair
        out[url] = profile
    return out
