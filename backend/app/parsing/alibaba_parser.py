import html
import json
import re
from typing import Optional

from ..models import Product

OFFER_LIST_MARKER = "window.__page__data_sse10._offer_list"
PRICE_RE = re.compile(r"([\d,.]+)")


def _extract_json_object(text: str, marker: str) -> Optional[dict]:
    """Find `<marker> = {...};` in a script blob and parse the balanced {...} object."""
    start = text.find(marker)
    if start == -1:
        return None
    eq = text.find("=", start)
    if eq == -1:
        return None
    i = eq + 1
    while i < len(text) and text[i] in " \t\n\r":
        i += 1
    if i >= len(text) or text[i] != "{":
        return None

    depth = 0
    in_str = False
    str_char = ""
    esc = False
    j = i
    while j < len(text):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == str_char:
                in_str = False
        else:
            if c in ("'", '"'):
                in_str = True
                str_char = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    j += 1
                    break
        j += 1

    raw = text[i:j]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _clean_title(raw_title: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", raw_title)
    return html.unescape(without_tags).strip()


def _normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    return url


def _parse_price(price_text: Optional[str]) -> tuple[Optional[float], Optional[float], Optional[str]]:
    if not price_text:
        return None, None, None
    currency = None
    if "$" in price_text:
        currency = "USD"
    numbers = [float(n.replace(",", "")) for n in PRICE_RE.findall(price_text)]
    if not numbers:
        return None, None, currency
    if len(numbers) == 1:
        return numbers[0], numbers[0], currency
    return min(numbers), max(numbers), currency


def parse_search_results(html_text: str) -> list[Product]:
    data = _extract_json_object(html_text, OFFER_LIST_MARKER)
    if not data:
        return []

    offers = (data.get("offerResultData") or {}).get("offers") or []
    products: list[Product] = []
    for offer in offers:
        product_url = _normalize_url(offer.get("productUrl"))
        if not product_url:
            continue
        price_min, price_max, currency = _parse_price(offer.get("price"))
        contact_url = _normalize_url(offer.get("contactSupplier"))
        products.append(
            Product(
                title=_clean_title(offer.get("title") or ""),
                image_url=offer.get("mainImage"),
                price_text=offer.get("price"),
                price_min=price_min,
                price_max=price_max,
                currency=currency,
                moq=offer.get("moq"),
                seller_name=offer.get("companyName"),
                seller_url=_normalize_url(offer.get("supplierHref") or offer.get("supplierHomeHref")),
                contact_type="form" if contact_url else None,
                contact_value=contact_url,
                product_url=product_url,
            )
        )
    return products
