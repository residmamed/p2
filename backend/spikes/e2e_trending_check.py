import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402

OUT_DIR = Path(__file__).parent


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 1000})
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        await page.goto("http://localhost:5173", wait_until="domcontentloaded")
        await page.wait_for_selector(".mode-tabs")

        await page.click("text=Trending")
        await page.wait_for_selector(".trending-view input[type=text]")
        await page.screenshot(path=str(OUT_DIR / "e2e_trending_1_initial.png"))

        await page.fill(".trending-view input[type=text]", "mid century modern bedroom")
        await page.click("text=Find Inspiration")

        await page.wait_for_selector("text=Finding inspiration", timeout=5000)
        await page.wait_for_selector("text=Finding inspiration", state="detached", timeout=60000)
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(OUT_DIR / "e2e_trending_2_inspiration.png"))

        tile_count = await page.locator(".inspiration-tile").count()
        print("Inspiration tiles:", tile_count)
        if tile_count == 0:
            print("No inspiration images — aborting")
            print("Console errors:", console_errors)
            await browser.close()
            return

        await page.click(".inspiration-tile >> nth=0")
        await page.wait_for_selector("text=Detecting items", timeout=5000)
        await page.wait_for_selector("text=Detecting items", state="detached", timeout=60000)
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(OUT_DIR / "e2e_trending_3_detected.png"))

        item_count = await page.locator(".detected-item").count()
        print("Detected items:", item_count)
        if item_count == 0:
            print("No detected items")
            print("Console errors:", console_errors)
            await browser.close()
            return

        await page.click(".detected-item >> nth=0")
        await page.screenshot(path=str(OUT_DIR / "e2e_trending_4_selected.png"))

        await page.click(".search-selected-button")
        await page.wait_for_selector("text=Searching", timeout=5000)
        await page.wait_for_selector("text=Searching…", state="detached", timeout=240000)
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(OUT_DIR / "e2e_trending_5_results.png"), full_page=False)

        card_count = await page.locator(".product-card").count()
        badge_count = await page.locator(".provenance-badge").count()
        print("Result product cards:", card_count)
        print("Provenance badges:", badge_count)
        print("Console errors:", console_errors)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
