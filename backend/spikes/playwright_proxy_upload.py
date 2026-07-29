import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402

from app.config import settings  # noqa: E402

IMAGE_PATH = Path(__file__).parent / "test_image.jpg"
OUT_DIR = Path(__file__).parent


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            proxy={
                "server": "https://api.zyte.com:8014",
                "username": settings.zyte_api_key,
                "password": "",
            },
            headless=True,
            args=[
                "--ignore-certificate-errors",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context(
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await context.new_page()

        print("Navigating...")
        try:
            resp = await page.goto(
                "https://www.alibaba.com/picture/search.htm",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            print("goto status:", resp.status if resp else None)
        except Exception as e:
            print("goto error:", e)

        try:
            await page.wait_for_selector(
                "[data-search='switch-image-upload'], .header-tab-shade-input-item",
                timeout=15000,
            )
        except Exception as e:
            print("wait_for_selector (app rendered) failed:", e)

        title = await page.title()
        print("Page title after load:", title)
        await page.screenshot(path=str(OUT_DIR / "pw_after_load.png"))

        if "Captcha" in title or "captcha" in title.lower():
            print("BLOCKED by captcha on initial load")
            await browser.close()
            return

        clicked = False
        for selector in [
            "[data-search='switch-image-upload']",
            "svg[class*='image-search-icon']",
            "[aria-label='Image search']",
        ]:
            try:
                await page.click(selector, timeout=5000)
                clicked = True
                print("Clicked selector:", selector)
                break
            except Exception as e:
                print(f"selector {selector} failed: {e}")

        if not clicked:
            print("Could not click image search toggle")
            await browser.close()
            return

        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT_DIR / "pw_after_click.png"))

        try:
            file_input = page.locator("input[type=file]").first
            await file_input.set_input_files(str(IMAGE_PATH), timeout=10000)
            print("set_input_files succeeded")
        except Exception as e:
            print("set_input_files failed:", e)
            await browser.close()
            return

        await page.wait_for_timeout(6000)
        title2 = await page.title()
        print("Page title after upload:", title2)
        await page.screenshot(path=str(OUT_DIR / "pw_after_upload.png"))

        html = await page.content()
        (OUT_DIR / "pw_after_upload.html").write_text(html)
        print("HTML length:", len(html))

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
