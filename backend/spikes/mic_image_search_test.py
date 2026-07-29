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
        page = await context.new_page()
        all_responses = []
        page.on("response", lambda r: all_responses.append((r.url, r.status, r.headers.get("content-type", ""))))

        try:
            resp = await page.goto("https://www.made-in-china.com/", wait_until="domcontentloaded", timeout=45000)
            print("goto status:", resp.status if resp else None)
        except Exception as e:
            print("goto error:", e)
            await browser.close()
            return

        try:
            await page.wait_for_selector("a.upload-img-camera", timeout=15000)
            print("Found camera icon")
        except Exception as e:
            print("Could not find camera icon:", e)
            await page.screenshot(path=str(OUT_DIR / "mic_1_no_icon.png"))
            await browser.close()
            return

        try:
            await page.keyboard.press("Escape")
            mask = page.locator(".campaign-pop-mask")
            if await mask.count() > 0:
                close_btn = page.locator(".campaign-pop-mask [class*=close], .campaign-pop-mask [class*=Close]")
                if await close_btn.count() > 0:
                    await close_btn.first.click(timeout=3000)
                else:
                    await mask.evaluate("el => el.remove()")
        except Exception as e:
            print("popup dismiss attempt:", e)

        await page.wait_for_timeout(500)
        await page.click("a.upload-img-camera", force=True)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT_DIR / "mic_2_after_click.png"))

        try:
            file_input = page.locator("input[type=file]").first
            await file_input.set_input_files(str(IMAGE_PATH), timeout=10000)
            print("set_input_files succeeded")
        except Exception as e:
            print("set_input_files failed:", e)
            await browser.close()
            return

        start_url = page.url
        for i in range(6):
            await page.wait_for_timeout(2000)
            print(f"t+{(i+1)*2}s url:", page.url)

        await page.screenshot(path=str(OUT_DIR / "mic_3_after_upload.png"))
        html = await page.content()
        (OUT_DIR / "mic_after_upload.html").write_text(html)
        print("HTML length:", len(html))
        print("URL changed:", page.url != start_url)

        print("\nNon-static responses:")
        for url, status, ctype in all_responses:
            if not any(url.endswith(ext) for ext in [".css", ".js", ".woff2", ".png", ".svg", ".gif", ".webp", ".ico"]):
                print(status, ctype, url[:200])

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
