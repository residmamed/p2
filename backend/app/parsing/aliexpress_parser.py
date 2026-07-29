import json
from typing import Optional

from ..models import Product

INIT_DATA_MARKER = "window._dida_config_._init_data_="


def _extract_init_data(text: str) -> Optional[dict]:
    start = text.find(INIT_DATA_MARKER)
    if start == -1:
        return None
    data_key = text.find("data:", start)
    if data_key == -1:
        return None
    i = data_key + len("data:")
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


def _normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    return url


def parse_search_results(html_text: str) -> list[Product]:
    data = _extract_init_data(html_text)
    if not data:
        return []

    try:
        items = data["data"]["root"]["fields"]["mods"]["itemList"]["content"]
    except (KeyError, TypeError):
        return []

    products: list[Product] = []
    for item in items:
        product_id = item.get("productId")
        if not product_id:
            continue
        product_url = f"https://www.aliexpress.com/item/{product_id}.html"

        title = (item.get("title") or {}).get("displayTitle") or ""
        image_url = _normalize_url((item.get("image") or {}).get("imgUrl"))

        prices = item.get("prices") or {}
        sale_price = prices.get("salePrice") or prices.get("originalPrice") or {}
        price_text = sale_price.get("formattedPrice")
        price_min = sale_price.get("minPrice")
        currency = sale_price.get("currencyCode")

        products.append(
            Product(
                title=title.strip(),
                image_url=image_url,
                price_text=price_text,
                price_min=price_min,
                price_max=price_min,
                currency=currency,
                moq=None,
                seller_name=None,
                seller_url=None,
                contact_type="form" if product_url else None,
                contact_value=product_url,
                product_url=product_url,
            )
        )
    return products
