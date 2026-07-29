import asyncio
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
            args=["--ignore-certificate-errors", "--disable-blink-features=AutomationControlled"],
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

        try:
            resp = await page.goto("https://www.google.com/imghp", wait_until="domcontentloaded", timeout=45000)
            print("goto status:", resp.status if resp else None)
            print("title:", await page.title())
            print("url after goto:", page.url)
        except Exception as e:
            print("goto error:", e)
            await browser.close()
            return

        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(OUT_DIR / "lens_1_landing.png"))
        (OUT_DIR / "lens_1_landing.html").write_text(await page.content())

        # Look for a file input directly first (Lens often has a hidden <input type=file>
        # behind an "upload a file" button/dropzone).
        try:
            file_input = page.locator("input[type=file]").first
            await file_input.wait_for(state="attached", timeout=8000)
            print("Found input[type=file] directly on landing page")
        except Exception as e:
            print("No direct file input on landing page:", e)
            file_input = None

        if file_input is None:
            # Try common "upload" trigger text/buttons.
            for sel in [
                "[aria-label='Search by image']",
                "text=upload a file",
                "text=Upload a file",
                "[aria-label='Upload a file']",
                "[aria-label*='upload' i]",
            ]:
                try:
                    locator = page.locator(sel).first
                    await locator.wait_for(state="visible", timeout=5000)
                    box = await locator.bounding_box()
                    if box:
                        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    else:
                        await locator.click(timeout=5000)
                    print("Clicked selector:", sel)
                    break
                except Exception as click_err:
                    print("selector failed:", sel, click_err)
                    continue
            await page.wait_for_timeout(1500)
            await page.screenshot(path=str(OUT_DIR / "lens_2_after_click.png"))
            try:
                file_input = page.locator("input[type=file]").first
                await file_input.wait_for(state="attached", timeout=8000)
                print("Found input[type=file] after click")
            except Exception as e:
                print("Still no file input:", e)
                (OUT_DIR / "lens_2_after_click.html").write_text(await page.content())
                await browser.close()
                return

        try:
            await file_input.set_input_files(str(IMAGE_PATH), timeout=15000)
            print("set_input_files succeeded")
        except Exception as e:
            print("set_input_files failed:", e)
            await browser.close()
            return

        for i in range(6):
            await page.wait_for_timeout(2000)
            print(f"t+{(i+1)*2}s url:", page.url, "| title:", await page.title())

        await page.screenshot(path=str(OUT_DIR / "lens_3_after_upload.png"))
        html = await page.content()
        (OUT_DIR / "lens_3_after_upload.html").write_text(html)
        print("HTML length:", len(html))
        print("Contains 'captcha':", "captcha" in html.lower())
        print("Contains 'unusual traffic':", "unusual traffic" in html.lower())
        print("Contains 'sorry':", "/sorry/" in page.url)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
