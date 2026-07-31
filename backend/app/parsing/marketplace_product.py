"""Alibaba / 1688 / Taobao product pages -> the fields a sourcing card needs.

Step 2 of Lens Sourcing reads HTML, not JSON: Oxylabs' `alibaba` and `universal`
sources both return the page, and Alibaba is not one of the domains their
`parse: true` covers. So the structured schema is built here.

**Four sources of truth**, and the first one that answers a given field wins it:

    1. Embedded page state    The site's own hydration blob. Richest by far, and
                              on Alibaba the only honest source of price and MOQ
                              (see the ladder note below). Most likely to move.
    2. `og:` meta tags        A standard the site publishes for link previews.
                              Title and image, stable across redesigns.
    3. JSON-LD Product        schema.org. Brand — which on these B2B sites is
                              the factory — plus a price that needs care.
    4. Visible-text regex     Last resort, and localised, so it is confined to
                              the few phrasings each site actually ships.

Mixing them per-field rather than picking one parser per site is deliberate:
these pages A/B constantly, and a page that has dropped its hydration blob
usually still has its og: tags. Anything no source answers stays None. That
matters more than the field count — a missing MOQ is a gap the caller reports,
while an MOQ scraped off the wrong element is a number a buyer might place an
order against.

**Why the blob outranks the standards here, contrary to the obvious ordering.**
Measured on four live Alibaba product pages, 2026-07-29. JSON-LD advertises a
single `offers.price`, and that price is the *bottom* of the quantity ladder —
the rate you get at five thousand units:

    JSON-LD   "price": "2.99", "priceCurrency": "USD"
    priceList  $3.99 (24-199)   $3.59 (200-4,999)   $2.99 (5,000+)
    MOQ        24 pieces

Publishing "$2.99, MOQ 24 pieces" is therefore not a rounding error, it is the
wrong number: at the minimum order this supplier charges $3.99. So the ladder
wins, and both ends of it are reported as a range.

**Currency travels with the price, always.** The same URL fetched from a
different exit IP returned its ladder in Serbian dinar (`RSD 452.71`) while the
JSON-LD block still said `USD`. Taking a number from one source and a currency
from the other reports $452.71 for a $2.99 tumbler. So every price here is
parsed out of a single formatted string that carries its own symbol, and a
ladder whose rungs disagree about currency is discarded rather than mixed.

That rule stays even though the localisation itself is now pinned upstream —
`oxylabs_client.GEO_FOR_SITE` fixes Alibaba's exit to the US and measured 6/6
USD where an unpinned exit gave rand, lira and dollars in six requests. Pinning
makes the common case consistent; it does not make a page that *does* arrive
localised safe to misread, and this parser still sees 1688 and Taobao, which
are not pinned and quote CNY natively.

**Delisted products serve a complete, well-formed lie.** An Alibaba listing that
no longer exists still returns HTTP 200 with valid JSON-LD: `name: "Product Not
Available"`, `brand: "Alibaba"`, `price: "0.99"`, `availability: "InStock"`.
Nothing about the shape of that page says it is dead. It is caught by title, and
the marketplace's own name is never accepted as a supplier.

**Provenance.** `companyName`, `companyProfileUrl`, `priceList` and
`formatMinOrderQuantity` are confirmed against live Alibaba product pages
(2026-07-29: 7 of 7 pages parsed, suppliers correctly distinct across
categories). og: and JSON-LD are site-independent standards. The 1688 and Taobao
keys are NOT confirmed end-to-end — the same convention supplier_resolve.VERIFIED
uses. An unverified pattern that misses costs one field on one row, and the
caller falls back to what SerpApi already told it.
"""
import html as html_module
import json
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from parsel import Selector

# Both of Alibaba's price shapes: a single "US $1.20" and a "US $1.20 - $2.50"
# range, with or without the "US". Commas are thousands separators here.
_MONEY = r"(?:US\s*)?(?:\$|USD\s*|¥|CNY\s*|RMB\s*)\s*(\d[\d.,]*\d|\d)"
PRICE_RANGE_RE = re.compile(_MONEY + r"\s*(?:-|–|to)\s*" + _MONEY, re.I)
PRICE_SINGLE_RE = re.compile(_MONEY, re.I)

# "Min. Order: 100 Pieces", "Min Order 2 pieces", "MOQ: 500 sets".
MOQ_TEXT_RE = re.compile(
    r"(?:min(?:imum)?\.?\s*order(?:\s*quantity)?|moq)\s*[:：]?\s*(\d[\d,]*)\s*([A-Za-z]{2,20})?",
    re.I,
)
# 1688 and Taobao say it in Chinese: "起订量 2件" / "2件起订".
MOQ_CN_RE = re.compile(r"(?:起订量|最小起订量)\s*[:：]?\s*(\d[\d,]*)\s*([一-鿿]{0,3})")
MOQ_CN_SUFFIX_RE = re.compile(r"(\d[\d,]*)\s*([一-鿿]{1,3})\s*起(?:订|批)")

CURRENCY_SYMBOLS = {
    "$": "USD", "US$": "USD", "¥": "CNY", "￥": "CNY", "€": "EUR", "£": "GBP",
    "₹": "INR", "₽": "RUB", "₩": "KRW", "₺": "TRY", "R$": "BRL", "A$": "AUD", "C$": "CAD",
}

# Local shorthand these marketplaces actually print, mapped to ISO. Observed on
# live Alibaba pages while the exit IP roamed: `R 28,52` (rand) and `162.96 TL`
# (lira). Neither is three letters, so both were being dropped as unnameable.
SHORT_CURRENCY_CODES = {"R": "ZAR", "TL": "TRY", "RM": "MYR", "KR": "SEK", "ZL": "PLN"}

# A single formatted quote as the marketplace writes it, symbol or ISO code and
# number together: "$3.99", "€2.93", "RSD\xa0452.71", "US $1.20".
#
# General on purpose. The first version of this recognised only $ / ¥ / USD /
# CNY, which meant a page served in euros parsed to nothing, the whole quantity
# ladder was skipped, and the price silently fell back to JSON-LD's bottom rung
# — the exact misquote the ladder exists to prevent, reintroduced by a regex
# that was too narrow to notice. A currency this cannot name is better rejected
# (leaving the field empty) than mis-attributed.
# The number is captured whole — digits, dots and commas together — and only
# then handed to _as_float, which works out which separator is the decimal one.
# A pattern that stopped at the first comma read "1.234,56" as 1.234.
_NUMBER = r"\d[\d.,]*\d|\d"
# One letter is enough for a code: South African rand renders as `R 28,52`, and
# requiring two dropped the whole rung. Liberal is safe here because this only
# ever runs over strings the site already told us are prices.
MONEY_RE = re.compile(
    rf"(?P<code>[A-Z]{{1,4}}\s*\$?|[$€£¥￥₹₽₩₺])\s*(?P<value>{_NUMBER})"
    rf"|(?P<value2>{_NUMBER})\s*(?P<code2>[A-Z]{{1,4}}|[$€£¥￥₹₽₩₺])"
)

# A delisted Alibaba listing answers 200 with a fully-formed product page whose
# only tell is its title. Matched on the whole title rather than as a substring,
# so a genuine listing whose name happens to contain one of these phrases
# survives — killing a real row is the worse error of the two.
# Deliberately does NOT contain the empty string: a page with no <title> at all
# is a page we know nothing about, not a delisted product, and treating the two
# alike silently emptied every result whose HTML omitted the tag.
UNAVAILABLE_TITLES = {
    "product not available",
    "page not found",
    "404 not found",
    "error",
}

# The marketplace is not a supplier. JSON-LD `brand` on a dead Alibaba page is
# the literal string "Alibaba", and printing that where the factory's name goes
# is the confident-looking wrong answer this codebase exists to avoid. Same
# principle as supplier_profile's denylist for service@alibaba.com.
MARKETPLACE_NAMES = {
    "alibaba", "alibaba.com", "alibaba group", "1688", "1688.com",
    "taobao", "taobao.com", "tmall", "tmall.com", "aliexpress", "aliexpress.com",
    "阿里巴巴", "淘宝",
    "made-in-china", "made-in-china.com", "made in china", "made in china.com",
}

# "... Perfect For Office Gym Travel - Buy  Product on Alibaba.com" — the site
# appends its own SEO tail to og:title. Anchored to the end so it can only ever
# remove a trailing boilerplate phrase, never bite into the product's name.
TITLE_SUFFIX_RE = re.compile(
    r"\s*[-–|]\s*(?:buy\b.*?on\s+)?"
    r"(?:alibaba\.com|1688\.com|taobao\.com|made-in-china\.com|阿里巴巴|淘宝网?)\s*$",
    re.I,
)

# Made-in-China prefixes og:title with its own merchandising flag — measured
# 2026-07-30, both live pages came back as "[Hot Item] 40oz Double Wall...".
# It is the site talking about its own listing, not part of the product's name,
# and it would sort a results table by whichever products the site is promoting.
# Anchored to the start, and only the known flags, so it cannot eat a product
# name that legitimately opens with a bracket.
TITLE_PREFIX_RE = re.compile(
    r"^\s*\[\s*(?:hot\s*item|recommended|new\s*item|hot\s*sale|top\s*sale)\s*\]\s*",
    re.I,
)

# Alibaba's quantity ladder, and the pre-formatted MOQ that sits beside it.
# Confirmed on live pages 2026-07-29.
PRICE_LIST_RE = re.compile(r'"priceList"\s*:\s*(\[.*?\])', re.S)
FORMAT_MOQ_RE = re.compile(r'"formatMinOrderQuantity"\s*:\s*"([^"]+)"')

# The site localises the MOQ unit independently of the currency — a live run
# returned "$7.65 / MOQ 2 개" and "$9.84 / MOQ 10 Adet" in the same response,
# Korean and Turkish for "pieces" beside dollar prices. A sourcing table that a
# buyer scans down needs one word for one thing, so the unit is normalised when
# it is recognisably "pieces" in some language and dropped when it isn't —
# leaving the bare count, which is the part that matters and is never wrong.
# Units that mean something *other* than pieces (sets, cartons, metres) are
# genuinely different quantities and are passed through untouched.
PIECE_WORDS = {
    "piece", "pieces", "pcs", "pc", "unit", "units",
    "개", "件", "個", "个",
    "adet", "stück", "stuck", "pièce", "pièces", "piece(s)",
    "piezas", "pieza", "pezzi", "pezzo", "peças", "peca",
    "штук", "шт", "قطعة", "ชิ้น", "cái", "buah",
}
NON_PIECE_UNITS = {"set", "sets", "carton", "cartons", "box", "boxes", "bag", "bags",
                   "meter", "meters", "metre", "metres", "kg", "ton", "tons", "pair", "pairs",
                   "roll", "rolls", "sheet", "sheets", "pack", "packs", "dozen"}

# The site's hydration blob arrives inside an HTML attribute on some templates,
# so its quotes may be backslash-escaped. Matching both forms is what makes this
# work on the attribute-embedded pages as well as the plain <script> ones.
# Lifted from supplier_resolve._json_field, where it is live-confirmed.
def _blob_string(html: str, key: str) -> Optional[str]:
    match = re.search(rf'\\?"{key}\\?"\s*:\s*\\?"(.*?)(?<!\\)\\?"', html)
    if not match:
        return None
    value = match.group(1).replace("\\/", "/").replace('\\"', '"').replace("\\u002F", "/").strip()
    return value or None


def _blob_number(html: str, key: str) -> Optional[float]:
    match = re.search(rf'\\?"{key}\\?"\s*:\s*(-?[\d.]+)', html)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


@dataclass
class ParsedProduct:
    """What one product page could be made to say. Every field optional: this
    supplements Lens data rather than replacing it, and the caller merges."""

    title: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_url: Optional[str] = None
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    currency: Optional[str] = None
    price_text: Optional[str] = None
    moq: Optional[str] = None
    image_url: Optional[str] = None
    # Buyer rating of the listing, 0-5, and how many reviews are behind it.
    #
    # Alibaba-only in practice. Measured 2026-07-30 on live pages: Alibaba
    # embeds `averageStar` and `totalReviewCount`, while Made-in-China product
    # pages carry no rating in any form — no JSON-LD aggregateRating, no
    # rating-shaped keys. So a None here means "this site does not publish one",
    # which is why it must never be coerced to 0: the UI sorts on rating, and a
    # fabricated zero would rank a perfectly good factory below every rated one.
    rating: Optional[float] = None
    review_count: Optional[int] = None

    def is_empty(self) -> bool:
        """True when the page yielded nothing usable — a challenge page, a 404
        served as 200, or a redesign this parser hasn't caught up with. The
        caller treats it exactly like a failed fetch."""
        # rating/review_count deliberately excluded: a page that yielded only a
        # star figure told us nothing about the product or who makes it, and
        # treating that as a successful parse would put a bare rating on a row
        # with no title, price or supplier.
        return not any(
            (self.title, self.supplier_name, self.price_min, self.moq, self.image_url)
        )


# --- layer 1: og: meta -----------------------------------------------------

def _meta(selector: Selector, prop: str) -> Optional[str]:
    for query in (
        f'meta[property="{prop}"]::attr(content)',
        f'meta[name="{prop}"]::attr(content)',
    ):
        value = selector.css(query).get()
        if value and value.strip():
            return html_module.unescape(value.strip())
    return None


# --- layer 2: JSON-LD ------------------------------------------------------

def _json_ld_products(selector: Selector) -> list[dict]:
    """Every schema.org Product object on the page, @graph and arrays included."""
    found: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if any(str(t).lower() == "product" for t in types if t):
                found.append(node)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)

    for blob in selector.css('script[type="application/ld+json"]::text').getall():
        try:
            walk(json.loads(blob))
        except (ValueError, RecursionError):
            continue
    return found


def _from_json_ld(node: dict) -> ParsedProduct:
    parsed = ParsedProduct()
    name = node.get("name")
    if isinstance(name, str) and name.strip():
        parsed.title = name.strip()

    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    if isinstance(image, str) and image.strip():
        parsed.image_url = _absolute(image.strip())

    brand = node.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    if isinstance(brand, str) and brand.strip():
        parsed.supplier_name = brand.strip()

    offers = node.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if offers else None
    if isinstance(offers, dict):
        low = _as_float(offers.get("lowPrice") or offers.get("price"))
        high = _as_float(offers.get("highPrice") or offers.get("price"))
        if low is not None:
            parsed.price_min = low
            parsed.price_max = high if high is not None else low
        currency = offers.get("priceCurrency")
        if isinstance(currency, str) and currency.strip():
            parsed.currency = currency.strip().upper()

    # The standard, site-agnostic place a rating lives. None of the current
    # marketplaces populate it — checked live on both Alibaba and
    # Made-in-China — but it costs nothing and is the first thing a site that
    # starts publishing ratings would use.
    rating = node.get("aggregateRating")
    if isinstance(rating, dict):
        value = _as_float(rating.get("ratingValue"))
        # 0-5 or nothing. A site reporting on another scale (or a parse that
        # picked up a price) must not become a star count.
        if value is not None and 0 < value <= 5:
            parsed.rating = value
        count = _as_float(rating.get("reviewCount") or rating.get("ratingCount"))
        if count is not None and count >= 0:
            parsed.review_count = int(count)
    return parsed


def _as_float(value) -> Optional[float]:
    """Parse a number that may be written in any locale's convention.

    `float(text.replace(",", ""))` is the obvious version and it is catastrophic
    here. Alibaba renders its prices in the visitor's locale, and half the world
    writes the decimal point as a comma — so a live run returned a ladder rung
    of `9,84` (alongside `MOQ 10 Adet` and `MOQ 2 개`, which is how the locale
    gave itself away) and the naive parser read it as **984**. A hundredfold
    error on a supplier quote, displayed with a dollar sign, in the table a
    buyer uses to choose who to order from. The South African `R 28,52` seen
    earlier would have become 2852 the same way.

    So the separators are read rather than assumed:

        1,234.56  both     -> the *last* separator is the decimal one
        1.234,56  both     -> likewise, giving 1234.56
        9,84      comma    -> 2 trailing digits, so a decimal comma -> 9.84
        1,234     comma    -> 3 trailing digits, the grouping convention -> 1234
        452.71    dot      -> decimal
    """
    if value is None:
        return None
    text = str(value).strip().replace("\xa0", "").replace(" ", "")
    if not text:
        return None

    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        # Whichever comes last is the decimal separator; the other groups.
        decimal = "." if text.rfind(".") > text.rfind(",") else ","
        grouping = "," if decimal == "." else "."
        text = text.replace(grouping, "").replace(decimal, ".")
    elif has_comma:
        parts = text.split(",")
        # A single comma with anything other than a 3-digit tail is a decimal
        # comma. Three digits is the grouping convention ("1,234"), and there
        # is no way to tell that from European "1,234" meaning 1.234 — but a
        # price is far more often 1234 than 1.234, and this only decides the
        # ambiguous case.
        if len(parts) == 2 and len(parts[1]) != 3:
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(".") > 1:
        # "1.234.567" — dots can only be grouping here.
        text = text.replace(".", "")

    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _absolute(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


# --- layer 3: embedded page state ------------------------------------------

# Alibaba's own record for the seller of the page you are on. `companyName` is
# the live-confirmed one; the rest are the neighbouring keys in the same object.
ALIBABA_SUPPLIER_KEYS = ("companyName", "supplierName", "sellerCompanyName")
ALIBABA_TITLE_KEYS = ("subject", "productTitle", "productSubject")
ALIBABA_MOQ_KEYS = ("minOrderQuantity", "moq", "beginAmount", "minOrder")
ALIBABA_UNIT_KEYS = ("productUnit", "unit", "saleUnit")
# Buyer rating of the listing. Measured on a live Alibaba product page
# 2026-07-30: `"averageStar":"3.7"` alongside `"totalReviewCount":99`. Both
# spellings of the star key are accepted because the value appears twice on the
# page, once quoted and once not, and the numeric reader handles either.
#
# Made-in-China has no equivalent — checked the same day, its pages carry no
# JSON-LD aggregateRating and no rating-shaped key at all — so there is no
# CN_RATING_KEYS counterpart to add. That is a fact about the site, not an
# omission here.
ALIBABA_RATING_KEYS = ("averageStar", "avgStar", "starLevel")
ALIBABA_REVIEW_COUNT_KEYS = ("totalReviewCount", "reviewCount", "totalReviews")

# 1688 ships a Chinese-language equivalent; Taobao's shop name lives under
# `sellerNick` / `shopName`. Neither is confirmed against a live page.
CN_SUPPLIER_KEYS = ("companyName", "shopName", "sellerNick", "loginId", "supplierLoginId")
CN_TITLE_KEYS = ("subject", "title", "itemTitle")
CN_MOQ_KEYS = ("beginAmount", "minOrderQuantity", "moq", "startAmount")
CN_UNIT_KEYS = ("unit", "saleUnit")


def _clean_supplier(name: Optional[str]) -> Optional[str]:
    """Reject the marketplace's own name. See MARKETPLACE_NAMES."""
    if not name:
        return None
    cleaned = name.strip()
    if cleaned.lower().rstrip(".").strip() in MARKETPLACE_NAMES:
        return None
    return cleaned[:160] or None


# Made-in-China's product page names the seller in a `com-name` block whose
# anchor points at the company's own subdomain — confirmed 2026-07-30 on two
# live pages, both `<p class="com-name"><a href="https://<company>.en.made-in-
# china.com">`. Restricted to that host so a layout change can only ever return
# nothing, never send a buyer to whatever else the block came to hold.
MIC_SUPPLIER_LINK_SELECTORS = (
    ".com-name a::attr(href)",
    "[class*=com-name] a::attr(href)",
)


def _made_in_china_supplier_url(selector: Selector) -> Optional[str]:
    for css in MIC_SUPPLIER_LINK_SELECTORS:
        for href in selector.css(css).getall():
            href = (href or "").strip()
            if not href:
                continue
            absolute = _absolute(href)
            try:
                host = (urlparse(absolute).hostname or "").lower()
            except ValueError:
                continue
            # A company subdomain, not the marketplace's own front page — the
            # latter is a link to Made-in-China, which is not a supplier.
            if host.endswith(".made-in-china.com") and not host.startswith("www."):
                return absolute
    return None


def _clean_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    cleaned = TITLE_SUFFIX_RE.sub("", html_module.unescape(title)).strip()
    cleaned = TITLE_PREFIX_RE.sub("", cleaned).strip()
    return cleaned[:300] or None


def _is_unavailable(selector: Selector) -> bool:
    """True for the delisted-product placeholder — a 200 with a full page and
    no product on it. Reported to the caller as an unreadable page, so the row
    keeps its Lens data instead of quoting $0.99 for a product that is gone."""
    for candidate in (_meta(selector, "og:title"), selector.css("title::text").get()):
        if candidate and candidate.strip().lower() in UNAVAILABLE_TITLES:
            return True
    return False


def _money(text: str) -> tuple[Optional[float], Optional[str]]:
    """One formatted quote -> (value, currency), both from the same string.

    Returns (None, None) rather than a bare number when the currency can't be
    named: a price whose currency is unknown is not a price a buyer can act on,
    and defaulting it to USD is how a 452-dinar quote becomes a $452 one.
    """
    if not text:
        return None, None
    # Marketplaces separate code from number with a non-breaking space.
    normalized = text.replace("\xa0", " ").replace(" ", " ").strip()
    match = MONEY_RE.search(normalized)
    if not match:
        return None, None

    raw_value = match.group("value") or match.group("value2")
    raw_code = (match.group("code") or match.group("code2") or "").strip()
    value = _as_float(raw_value)
    if value is None:
        return None, None

    # "US $" and "US$" both mean dollars; take the symbol when one is present.
    for symbol, iso in CURRENCY_SYMBOLS.items():
        if symbol in raw_code:
            return value, iso
    code = raw_code.replace("$", "").strip().upper()
    # Marketplaces print local shorthand, not ISO: South African rand is `R`
    # and Turkish lira is `TL`. Requiring three letters dropped those rungs and
    # with them the whole ladder, falling the price back to JSON-LD's floor.
    normalized = SHORT_CURRENCY_CODES.get(code)
    if normalized:
        return value, normalized
    # Anything else alphabetic is passed through as the site wrote it rather
    # than guessed at. An unfamiliar-but-labelled currency is honest; silently
    # calling it dollars is the failure this whole function exists to avoid.
    if 2 <= len(code) <= 4 and code.isalpha():
        return value, code
    return value, None


MOQ_PARTS_RE = re.compile(r"^\s*(\d[\d.,]*)\s*(.*)$", re.S)


def _normalize_moq(raw: str) -> Optional[str]:
    """"2 개" -> "2 pieces", "10 Adet" -> "10 pieces", "600 sets" -> unchanged.

    The count is the load-bearing part and survives whatever happens to the
    unit. A word that means pieces in some language becomes "pieces"; a word
    that means something else is a genuinely different quantity and is left
    alone; an unrecognised word is dropped rather than shown, because a unit a
    buyer cannot read is worse than no unit beside a number they can.
    """
    match = MOQ_PARTS_RE.match(raw.strip())
    if not match:
        return raw.strip()[:60] or None

    count_text, unit = match.group(1), match.group(2).strip()
    count = _as_float(count_text)
    # MOQs arrive as "2.0" in the blob and as "2" in the formatted string; a
    # fractional minimum order is a mis-keyed field, not a real quantity.
    count_str = str(int(count)) if count is not None and count == int(count) else count_text

    if not unit:
        return count_str
    key = unit.lower().strip(".")
    if key in PIECE_WORDS:
        return f"{count_str} pieces"
    if key in NON_PIECE_UNITS or key.rstrip("s") in NON_PIECE_UNITS:
        return f"{count_str} {unit}"[:60]
    # Unrecognised and non-Latin: keep the number, lose the word.
    if not unit.isascii():
        return count_str
    return f"{count_str} {unit}"[:60]


def _ladder(html: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Alibaba's quantity-price ladder, as (min, max, currency).

    Both the value and the symbol come out of the same `formatPrice` string, so
    a localised page reports a consistent localised quote rather than a foreign
    number wearing a dollar sign. Rungs that disagree about currency mean the
    page mixed locales mid-render; that is discarded rather than reconciled.
    """
    match = PRICE_LIST_RE.search(html)
    if not match:
        return None, None, None
    try:
        rows = json.loads(match.group(1))
    except (ValueError, RecursionError):
        return None, None, None

    values: list[float] = []
    currencies: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        formatted = row.get("formatPrice")
        if not isinstance(formatted, str):
            continue
        value, currency = _money(formatted)
        if value is None or currency is None:
            continue
        values.append(value)
        currencies.add(currency)

    # One unnameable rung is a parse gap; rungs in two currencies mean the page
    # mixed locales mid-render, and a range spanning both would be nonsense.
    if not values or len(currencies) != 1:
        return None, None, None
    return min(values), max(values), currencies.pop()


def _first_blob_string(html: str, keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = _blob_string(html, key)
        # A hydration blob also carries i18n templates, so a "value" that is
        # just the key echoed back is a placeholder, not data — the same trap
        # supplier_resolve hit with contactName: "name".
        if value and value.lower() not in {k.lower() for k in keys} and len(value) > 1:
            return value
    return None


def _first_blob_number(html: str, keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        value = _blob_number(html, key)
        if value is not None and value > 0:
            return value
    return None


# --- layer 4: visible text --------------------------------------------------

def _price_from_text(text: str) -> tuple[Optional[float], Optional[float], Optional[str]]:
    range_match = PRICE_RANGE_RE.search(text)
    if range_match:
        low = _as_float(range_match.group(1))
        high = _as_float(range_match.group(2))
        if low is not None and high is not None:
            return min(low, high), max(low, high), _currency_in(range_match.group(0))
    single = PRICE_SINGLE_RE.search(text)
    if single:
        value = _as_float(single.group(1))
        if value is not None:
            return value, value, _currency_in(single.group(0))
    return None, None, None


def _currency_in(text: str) -> Optional[str]:
    upper = text.upper()
    if "¥" in text or "￥" in text or "CNY" in upper or "RMB" in upper:
        return "CNY"
    if "$" in text or "USD" in upper:
        return "USD"
    return None


def _moq_from_text(text: str) -> Optional[str]:
    match = MOQ_TEXT_RE.search(text)
    if match:
        unit = (match.group(2) or "").strip()
        return f"{match.group(1)} {unit}".strip()
    for pattern in (MOQ_CN_RE, MOQ_CN_SUFFIX_RE):
        match = pattern.search(text)
        if match:
            unit = (match.group(2) or "").strip()
            return f"{match.group(1)}{unit}".strip()
    return None


# --- the merge --------------------------------------------------------------

def _fill(target: ParsedProduct, source: ParsedProduct) -> None:
    """First writer wins each field — layers are applied most-trusted first.

    NOTE: this list is the whole contract. A field added to ParsedProduct and
    populated by a layer but not named here is silently discarded, and the
    symptom is indistinguishable from the site not publishing it — which is how
    `rating` first "proved" that Alibaba has no ratings, on pages that were
    serving `averageStar` all along. Add the field here when you add it above.
    """
    for field in ("title", "supplier_name", "supplier_url", "currency", "moq", "image_url", "price_text"):
        if getattr(target, field) is None and getattr(source, field) is not None:
            setattr(target, field, getattr(source, field))
    if target.price_min is None and source.price_min is not None:
        target.price_min = source.price_min
        target.price_max = source.price_max
        if source.currency and target.currency is None:
            target.currency = source.currency
    # Moved as a pair, like price. A review count belongs to the rating it was
    # counted for, so taking one layer's stars and another's count would report
    # a number of reviews that never backed that figure.
    if target.rating is None and source.rating is not None:
        target.rating = source.rating
        target.review_count = source.review_count


def parse_product_page(html: str, site: str) -> ParsedProduct:
    """Read one Alibaba / 1688 / Taobao product page.

    Returns a ParsedProduct that may be entirely empty — check `is_empty()`.
    Never raises: a malformed page is a row that keeps its Lens data, not a
    failed request.
    """
    if not html or not html.strip():
        return ParsedProduct()
    try:
        return _parse(html, site)
    except Exception:  # noqa: BLE001 - a parser bug must not sink an enrichment batch
        return ParsedProduct()


def _parse(html: str, site: str) -> ParsedProduct:
    selector = Selector(text=html)

    # 0. Is there a product on this page at all? A delisted listing serves a
    # complete, valid, entirely fictional one — check before reading any of it.
    if _is_unavailable(selector):
        return ParsedProduct()

    parsed = ParsedProduct()

    # 1. the site's own state: the quantity ladder and the MOQ beside it. First
    # because JSON-LD's single price is the ladder's bottom rung and quoting it
    # against the MOQ misstates the cost — see the module docstring.
    low, high, currency = _ladder(html)
    if low is not None:
        parsed.price_min, parsed.price_max, parsed.currency = low, high, currency
    formatted_moq = FORMAT_MOQ_RE.search(html)
    if formatted_moq:
        parsed.moq = _normalize_moq(formatted_moq.group(1))

    # 2. og:
    parsed.title = _clean_title(_meta(selector, "og:title"))
    parsed.image_url = _meta(selector, "og:image") or None
    if parsed.image_url:
        parsed.image_url = _absolute(parsed.image_url)
    og_price = _meta(selector, "og:price:amount") or _meta(selector, "product:price:amount")
    if og_price and parsed.price_min is None:
        value = _as_float(og_price)
        if value is not None:
            parsed.price_min = parsed.price_max = value
            parsed.currency = (
                _meta(selector, "og:price:currency")
                or _meta(selector, "product:price:currency")
                or None
            )

    # 3. JSON-LD
    for node in _json_ld_products(selector):
        _fill(parsed, _from_json_ld(node))

    # 4. embedded page state
    is_alibaba = site == "alibaba"
    supplier_keys = ALIBABA_SUPPLIER_KEYS if is_alibaba else CN_SUPPLIER_KEYS
    title_keys = ALIBABA_TITLE_KEYS if is_alibaba else CN_TITLE_KEYS
    moq_keys = ALIBABA_MOQ_KEYS if is_alibaba else CN_MOQ_KEYS
    unit_keys = ALIBABA_UNIT_KEYS if is_alibaba else CN_UNIT_KEYS

    blob = ParsedProduct(
        supplier_name=_first_blob_string(html, supplier_keys),
        title=_first_blob_string(html, title_keys),
        supplier_url=_blob_string(html, "companyProfileUrl"),
    )
    moq_count = _first_blob_number(html, moq_keys)
    if moq_count is not None:
        unit = _first_blob_string(html, unit_keys) or ""
        # Alibaba states MOQ as a bare integer with the unit alongside; a
        # fractional "minimum order" is a mis-keyed field, not a real quantity.
        blob.moq = f"{int(moq_count)} {unit}".strip() if moq_count == int(moq_count) else None
    if is_alibaba:
        # Bounded to 0-5 rather than trusted: these keys sit in a page-state blob
        # next to prices and counts, and a loose regex that caught the wrong one
        # would put a 99 or a 7.20 in the stars column. Out-of-range means "this
        # wasn't the rating" and is dropped, not clamped.
        star = _first_blob_number(html, ALIBABA_RATING_KEYS)
        if star is not None and 0 < star <= 5:
            blob.rating = round(star, 2)
        reviews = _first_blob_number(html, ALIBABA_REVIEW_COUNT_KEYS)
        # A review count with no rating behind it is not evidence of anything,
        # and 0 reviews must stay absent rather than becoming "0 reviews" — the
        # UI's review-weighted sort reads the two together.
        if reviews is not None and reviews > 0 and blob.rating is not None:
            blob.review_count = int(reviews)
    _fill(parsed, blob)

    # 4b. Made-in-China's company link. It ships no `companyProfileUrl` blob, so
    # without this the supplier's name arrives from JSON-LD `brand` with nothing
    # to click — and the company page is where the contact details are. The name
    # is already correct by this point, so this only ever adds the href.
    if site == "made_in_china" and not parsed.supplier_url:
        parsed.supplier_url = _made_in_china_supplier_url(selector)

    # 5. visible text, over the body only — the <head> carries marketing copy
    # and the site's own adverts, which is where a stray price would come from.
    if parsed.price_min is None or parsed.moq is None:
        body_text = " ".join(
            selector.css("body ::text").getall()[:4000]
        )
        body_text = re.sub(r"\s+", " ", body_text)[:200_000]
        if parsed.price_min is None:
            low, high, currency = _price_from_text(body_text)
            if low is not None:
                parsed.price_min, parsed.price_max = low, high
                parsed.currency = parsed.currency or currency
        if parsed.moq is None:
            parsed.moq = _moq_from_text(body_text)

    parsed.price_text = _format_price(parsed.price_min, parsed.price_max, parsed.currency)
    parsed.title = _clean_title(parsed.title)
    # Applied last so it catches a marketplace name from any layer — JSON-LD
    # `brand` is where "Alibaba" arrives, but the blob could serve it too.
    parsed.supplier_name = _clean_supplier(parsed.supplier_name)
    return parsed


def _format_price(low: Optional[float], high: Optional[float], currency: Optional[str]) -> Optional[str]:
    if low is None:
        return None
    symbol = {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£"}.get(currency or "", "")
    if not symbol and currency:
        symbol = f"{currency} "
    if high is not None and high > low:
        return f"{symbol}{low:,.2f} - {symbol}{high:,.2f}"
    return f"{symbol}{low:,.2f}"
