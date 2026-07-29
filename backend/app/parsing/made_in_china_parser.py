import re
from typing import Optional

from parsel import Selector

from ..models import Product

PRICE_RE = re.compile(r"([\d,.]+)")


def _normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _parse_price(price_text: Optional[str]) -> tuple[Optional[float], Optional[float], Optional[str]]:
    if not price_text:
        return None, None, None
    currency = "USD" if "US$" in price_text else None
    numbers = [float(n.replace(",", "")) for n in PRICE_RE.findall(price_text)]
    if not numbers:
        return None, None, currency
    if len(numbers) == 1:
        return numbers[0], numbers[0], currency
    return min(numbers), max(numbers), currency


def _first(node, *selectors: str) -> Optional[str]:
    for selector in selectors:
        value = node.css(selector).get()
        if value:
            return value
    return None


# Made-in-China lazy-loads product photos: `src` holds a 1x1 spacer (or the
# supplier's logo) until the card scrolls into view, and the real photo sits in
# data-original / data-src / data-lazyload. Reading `src` first made every
# listing on an image-search page share one placeholder URL — which then hashed
# identically, so visual matching scored all 80 results the same. Placeholder
# hosts are rejected outright rather than ranked.
PLACEHOLDER_IMAGE_RE = re.compile(r"micstatic\.com|/space\.png|blank\.gif|data:image", re.I)

IMAGE_ATTR_SELECTORS = (
    "img::attr(data-original)",
    "img::attr(data-src)",
    "img::attr(data-lazyload)",
    "img::attr(data-echo)",
    "img::attr(src)",
)
IMAGE_CONTAINERS = ("div.prod-img", ".img-wrap", ".prod-image", "")


def _product_image(node) -> Optional[str]:
    """Pick the real photo, preferring lazy-load attributes over `src` and
    skipping known placeholders. Containers are tried in order because the
    image-search results page uses a different card layout from the text-search
    grid."""
    for container in IMAGE_CONTAINERS:
        for attr in IMAGE_ATTR_SELECTORS:
            selector = f"{container} {attr}".strip()
            for value in node.css(selector).getall():
                if value and not PLACEHOLDER_IMAGE_RE.search(value):
                    return value
    return None


def _supplier_url_from_product(product_url: Optional[str]) -> Optional[str]:
    """Derive the supplier's own page from a product URL.

    Every Made-in-China product lives at {subdomain}.en.made-in-china.com/..., so
    the company page is always reachable from the origin even when the results
    card doesn't link it — which it often doesn't on image-search pages. Without
    this the whole supplier-enrichment stage had nothing to fetch.
    """
    if not product_url:
        return None
    normalized = _normalize_url(product_url) or ""
    match = re.match(r"(https://[a-z0-9\-]+\.en\.made-in-china\.com)/", normalized, re.I)
    return f"{match.group(1)}/contact-info.html" if match else None


def _parse_node(node) -> Optional[Product]:
    product_url = _first(node, "h2.product-name > a::attr(href)", ".product-name a::attr(href)")
    if not product_url:
        return None
    title = _first(node, "h2.product-name > a::attr(title)", ".product-name a::attr(title)") or ""

    price_parts = node.css("strong.price ::text, strong.price::text").getall()
    price_text = "".join(price_parts).strip() or None
    price_min, price_max, currency = _parse_price(price_text)

    moq = _first(
        node,
        "div.product-property .info:not(.price-info)::text",
        ".attr-item .attribute::text",
    )
    moq = moq.strip() if moq else None

    image_url = _product_image(node)

    seller_name = _first(
        node,
        ".compnay-name-li a.compnay-name span::text",
        ".company-name a.compnay-name span::text",
    )
    seller_url = _normalize_url(
        _first(node, ".compnay-name-li a.compnay-name::attr(href)", ".company-name a.compnay-name::attr(href)")
    ) or _supplier_url_from_product(product_url)

    contact_url = _normalize_url(node.css("a[href*='sendInquiry']::attr(href)").get())

    return Product(
        title=title.strip(),
        image_url=_normalize_url(image_url),
        price_text=price_text,
        price_min=price_min,
        price_max=price_max,
        currency=currency,
        moq=moq,
        seller_name=seller_name.strip() if seller_name else None,
        seller_url=seller_url,
        contact_type="form" if contact_url else None,
        contact_value=contact_url,
        product_url=_normalize_url(product_url),
    )


def parse_search_results(html_text: str) -> list[Product]:
    sel = Selector(html_text)
    nodes = sel.css("div.list-node") or sel.css(".products-item")

    products: list[Product] = []
    for node in nodes:
        product = _parse_node(node)
        if product:
            products.append(product)
    return products
