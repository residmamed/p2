"""Go/no-go: can Browserbase reach the two retail sites Zyte can't?

Zyte returns 0 products for Temu and a 520 website-ban for Costco, so those two
need the cloud browser that already drives the supplier upload widgets. This
spike loads each site's search page in a real session and reports what the DOM
actually holds — product count, and crucially whether the page publishes a
demand signal (Temu's "N sold", Costco's ratings) that can stand in for the
best-seller sort neither site offers in a plain URL.

Nothing downstream is worth building until this prints real products.

    python -m spikes.probe_retail_browserbase
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import browserbase_client as bb  # noqa: E402

Q = "insulated water bottle"
OUT = Path(__file__).parent / "out" / "retail"

TARGETS = {
    "temu": f"https://www.temu.com/search_result.html?search_key={Q.replace(' ', '+')}",
    "costco": f"https://www.costco.com/CatalogSearch?keyword={Q.replace(' ', '+')}",
}

# Pulls every anchor that looks like a product, with its card text, so the real
# selectors can be read off live output instead of guessed.
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
      text: text.slice(0, 220),
      img: img ? (img.getAttribute('src') || img.getAttribute('data-src') || '') : '',
    });
  }
  return { count: out.length, title: document.title, items: out.slice(0, 400) };
}
"""

SOLD_RE = re.compile(r"([\d.,]+\s*[KkMm]?\+?)\s*(?:sold|bought)", re.I)
RATING_RE = re.compile(r"\b([0-5]\.\d)\b")
PRICE_RE = re.compile(r"\$\s?([\d,]+\.\d{2})")


CHALLENGE_MARKERS = ("security verification", "are you a robot", "captcha", "access denied", "verify you are")


async def _wait_out_challenge(page, budget_ms: int = 75_000) -> str:
    """Browserbase solves captchas automatically but asynchronously — it signals
    start/finish over the console. Waiting on those (with a plain timeout
    fallback) is the difference between reading a challenge page and reading
    results."""
    solving = {"done": False}

    def on_console(msg):
        text = (msg.text or "").lower()
        if "browserbase-solving-finished" in text:
            solving["done"] = True

    page.on("console", on_console)
    waited = 0
    while waited < budget_ms:
        await page.wait_for_timeout(2500)
        waited += 2500
        title = ((await page.title()) or "").lower()
        if solving["done"] or not any(m in title for m in CHALLENGE_MARKERS):
            # Give the post-solve navigation a moment to land.
            await page.wait_for_timeout(4000)
            return await page.title() or ""
    return await page.title() or ""


async def probe(site: str, url: str) -> None:
    print(f"\n{'='*70}\n{site}  {url}")
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        async with bb.remote_browser() as rb:
            print(f"  session https://browserbase.com/sessions/{rb.session_id}")
            page = rb.page
            await page.goto(url, wait_until="commit", timeout=60_000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=45_000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)

            title = ((await page.title()) or "").lower()
            if any(m in title for m in CHALLENGE_MARKERS):
                print(f"  challenge hit ({title[:50]!r}) — waiting for auto-solve…")
                resolved = await _wait_out_challenge(page)
                print(f"  after solve: {resolved[:70]!r}")

            # These grids lazy-load; scroll to pull more cards into the DOM.
            for _ in range(4):
                try:
                    await page.mouse.wheel(0, 4000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)

            try:
                data = await page.evaluate(HARVEST_JS)
            except Exception:
                # A late redirect destroys the execution context; settle and retry once.
                await page.wait_for_timeout(6000)
                data = await page.evaluate(HARVEST_JS)
            await page.screenshot(path=OUT / f"{site}.png", full_page=False)
            (OUT / f"{site}.html").write_text(await page.content())

            print(f"  title   {data['title'][:80]!r}")
            print(f"  anchors {data['count']}")

            sold = [i for i in data["items"] if SOLD_RE.search(i["text"])]
            priced = [i for i in data["items"] if PRICE_RE.search(i["text"])]
            rated = [i for i in data["items"] if RATING_RE.search(i["text"])]

            print(f"  cards with price:  {len(priced)}")
            print(f"  cards with SOLD:   {len(sold)}   <- demand signal")
            print(f"  cards with rating: {len(rated)}")

            for i in (sold or priced)[:4]:
                m = SOLD_RE.search(i["text"])
                print(f"    - sold={m.group(1) if m else '-':<8} {i['text'][:90]}")
                print(f"      {i['href'][:100]}")
    except Exception as e:
        print(f"  FAILED {type(e).__name__}: {str(e)[:200]}")


async def main() -> None:
    if not bb.is_configured():
        raise SystemExit("Browserbase not configured in backend/.env")
    for site, url in TARGETS.items():
        await probe(site, url)
    print(f"\nartifacts in {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
