"""Retail extraction for the sites Zyte can't reach.

Measured, not assumed (spikes/probe_bestseller_sorts.py, probe_hard_sites.py):

    Walmart, Amazon, IKEA   Zyte productList works -> bestsellers.py handles them
    Temu                    Zyte returns 0 products in every mode
    Costco                  Zyte returns HTTP 520 "Website Ban"

So those two get the same treatment the supplier upload widgets get: a real
cloud browser. The difference from the supplier pipeline is that there's no
results URL to hand back to Zyte afterwards — Zyte is banned from these hosts
entirely — so the browser has to do the extraction too.

Extraction is DOM-based and deliberately generic: harvest every product-looking
anchor with its card text, then regex the fields out. Per their own scraping
notes, an a11y/LLM extract drops href and img src (which the app needs for
export and thumbnails), while the DOM has both. Card class names on these two
sites are hashed and rotate; link shape and card text don't.

Temu publishes a "N sold" count per card. That's a *better* best-seller signal
than any sort option — it's actual recent demand rather than a ranking someone
else computed — and it's what stands in for the sort neither site offers.
"""
import asyncio
import re
from dataclasses import dataclass

from . import browserbase_client as bb
from .models import Product
from .product_images import best_image

CHALLENGE_MARKERS = (
    "security verification",
    "are you a robot",
    "captcha",
    "access denied",
    "verify you are",
    "pardon our interruption",
)

# Browserbase solves captchas automatically but asynchronously, signalling over
# the console. Waiting on that signal is the difference between reading a
# challenge page and reading results.
SOLVE_FINISHED = "browserbase-solving-finished"
CHALLENGE_BUDGET_MS = 75_000
SCROLL_ROUNDS = 5

SOLD_RE = re.compile(r"([\d][\d.,]*)\s*([KkMm])?\+?\s*(?:sold|bought)", re.I)
PRICE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")
RATING_RE = re.compile(r"\b([0-5](?:\.\d)?)\s*(?:out of 5|stars?|★)", re.I)
REVIEWS_RE = re.compile(r"\(?([\d,]+)\)?\s*(?:reviews?|ratings?)", re.I)

HARVEST_JS = """
() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href;
    if (!href || seen.has(href)) continue;
    const card = a.closest('div,li,article') || a;
    const text = (card.innerText || '').trim().replace(/\\s+/g, ' ');
    if (text.length < 8 || text.length > 400) continue;
    const img = card.querySelector('img');
    seen.add(href);
    out.push({
      href,
      text: text.slice(0, 260),
      img: img ? (img.getAttribute('src') || img.getAttribute('data-src')
                  || img.getAttribute('data-lazy-src') || '') : '',
    });
  }
  return { title: document.title, items: out.slice(0, 400) };
}
"""


@dataclass(frozen=True)
class BrowserSite:
    id: str
    label: str
    search_url: str
    # Substring every real product URL on this site contains. The single most
    # reliable filter available — nav, promo and category links never match it.
    product_href: str


BROWSER_SITES: dict[str, BrowserSite] = {
    "temu": BrowserSite(
        "temu", "Temu",
        "https://www.temu.com/search_result.html?search_key={q}",
        product_href="/goods",
    ),
    "costco": BrowserSite(
        "costco", "Costco",
        "https://www.costco.com/CatalogSearch?keyword={q}",
        product_href=".product.",
    ),
}


def _parse_sold(text: str) -> int | None:
    """Turn "10K+ sold" / "1,234 bought" into a number. This is the demand
    signal that substitutes for a best-seller sort on sites that have none."""
    m = SOLD_RE.search(text)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000
    return int(value)


def _parse_price(text: str) -> tuple[str | None, float | None]:
    m = PRICE_RE.search(text)
    if not m:
        return None, None
    raw = m.group(0).strip()
    try:
        return raw, float(m.group(1).replace(",", ""))
    except ValueError:
        return raw, None


def _title_from_card(text: str) -> str:
    """Card text starts with the product name on both sites; price/rating/sold
    follow. Cut at the first of those markers rather than guessing a selector."""
    cut = len(text)
    for pattern in (PRICE_RE, SOLD_RE, RATING_RE):
        m = pattern.search(text)
        if m:
            cut = min(cut, m.start())
    title = text[:cut].strip(" -|·,")
    return title or text[:80]


def harvest_to_products(items: list[dict], site: BrowserSite) -> list[Product]:
    """Pure function over harvested DOM rows — unit-testable without a browser."""
    products: list[Product] = []
    seen: set[str] = set()
    for item in items:
        href = item.get("href") or ""
        if site.product_href not in href or href in seen:
            continue
        text = item.get("text") or ""
        title = _title_from_card(text)
        if len(title) < 8:
            continue
        seen.add(href)

        price_text, price_min = _parse_price(text)
        rating_m = RATING_RE.search(text)
        reviews_m = REVIEWS_RE.search(text)

        products.append(
            Product(
                site=site.id,
                title=title[:300],
                product_url=href,
                image_url=best_image(item.get("img"), site=site.id),
                price_text=price_text,
                price_min=price_min,
                currency="USD" if price_text else None,
                rating=float(rating_m.group(1)) if rating_m else None,
                review_count=int(reviews_m.group(1).replace(",", "")) if reviews_m else None,
                # sold count rides in popularity_score — it IS the demand metric
                # for these sites; bestsellers.py reads it as such.
                popularity_score=_parse_sold(text),
            )
        )
    return products


async def _settle_challenge(page) -> str:
    solving = {"done": False}

    def on_console(msg):
        if SOLVE_FINISHED in (msg.text or "").lower():
            solving["done"] = True

    page.on("console", on_console)
    waited = 0
    while waited < CHALLENGE_BUDGET_MS:
        await page.wait_for_timeout(2500)
        waited += 2500
        title = ((await page.title()) or "").lower()
        if solving["done"] or not any(m in title for m in CHALLENGE_MARKERS):
            await page.wait_for_timeout(4000)
            break
    return (await page.title()) or ""


async def fetch_site(site: BrowserSite, query: str) -> tuple[list[Product], list[str]]:
    """Load one Zyte-banned retail site in a cloud browser and extract products.

    Returns ([], warnings) on any failure — a challenged site contributes a
    warning, never a silent empty grid.
    """
    url = site.search_url.format(q=query.replace(" ", "+"))
    warnings: list[str] = []

    try:
        async with bb.remote_browser() as rb:
            page = rb.page
            # "commit" rather than "domcontentloaded": these pages bounce through
            # an interstitial, and waiting for DCL on the doomed document throws.
            await page.goto(url, wait_until="commit", timeout=60_000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=45_000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)

            title = ((await page.title()) or "").lower()
            if any(m in title for m in CHALLENGE_MARKERS):
                title = (await _settle_challenge(page)).lower()
                if any(m in title for m in CHALLENGE_MARKERS):
                    return [], [
                        f"[{site.label}] blocked by a bot challenge that didn't clear "
                        f"(session {rb.session_id}) — no results from this site."
                    ]

            for _ in range(SCROLL_ROUNDS):
                try:
                    await page.mouse.wheel(0, 4000)
                except Exception:
                    break
                await page.wait_for_timeout(1500)

            try:
                data = await page.evaluate(HARVEST_JS)
            except Exception:
                # A late redirect destroys the execution context; settle, retry once.
                await page.wait_for_timeout(6000)
                data = await page.evaluate(HARVEST_JS)

            products = harvest_to_products(data.get("items", []), site)
            if not products:
                warnings.append(
                    f"[{site.label}] page loaded but no product cards matched "
                    f"(session {rb.session_id}) — selectors may need re-tuning."
                )
            return products, warnings

    except bb.BrowserbaseError as e:
        return [], [f"[{site.label}] {e}"]
    except Exception as e:  # noqa: BLE001 - one site must never sink the search
        return [], [f"[{site.label}] browser error: {type(e).__name__}: {str(e)[:160]}"]


async def fetch_sites(site_ids: list[str], query: str, concurrency: int = 2) -> tuple[list[Product], list[str]]:
    sem = asyncio.Semaphore(concurrency)

    async def _one(sid: str):
        async with sem:
            return await fetch_site(BROWSER_SITES[sid], query)

    results = await asyncio.gather(*(_one(s) for s in site_ids if s in BROWSER_SITES))
    products: list[Product] = []
    warnings: list[str] = []
    for p, w in results:
        products.extend(p)
        warnings.extend(w)
    return products, warnings
