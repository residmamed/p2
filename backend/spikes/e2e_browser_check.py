import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402

OUT_DIR = Path(__file__).parent
IMAGE_PATH = OUT_DIR / "test_image.jpg"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        await page.goto("http://localhost:5173", wait_until="domcontentloaded")
        await page.wait_for_selector("input[type=text]", timeout=10000)
        await page.screenshot(path=str(OUT_DIR / "e2e_1_initial.png"))

        await page.fill("input[type=text]", "wireless earbuds")
        await page.click("button[type=submit]")

        await page.wait_for_selector("text=Searching", timeout=5000)
        await page.wait_for_selector("text=Searching", state="detached", timeout=90000)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=str(OUT_DIR / "e2e_2_text_results.png"), full_page=False)

        broken_images = await page.evaluate(
            "Array.from(document.querySelectorAll('.product-image-link img')).filter(i => !i.complete || i.naturalWidth === 0).length"
        )
        print("Broken/unloaded images:", broken_images, "of", await page.locator(".product-image-link img").count())

        card_count = await page.locator(".product-card").count()
        print("Text search product cards rendered:", card_count)

        print("Console errors so far:", console_errors)

        # Image search flow
        async with page.expect_file_chooser() as fc_info:
            await page.click(".camera-button")
        file_chooser = await fc_info.value
        await file_chooser.set_files(str(IMAGE_PATH))

        await page.wait_for_selector("text=Searching", timeout=5000)
        await page.wait_for_selector("text=Searching", state="detached", timeout=180000)
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(OUT_DIR / "e2e_3_image_results.png"), full_page=False)

        image_card_count = await page.locator(".product-card").count()
        print("Image search product cards rendered:", image_card_count)
        print("Console errors after image search:", console_errors)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
