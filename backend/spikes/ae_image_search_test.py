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
        all_responses = []
        json_bodies = {}

        def on_response(response):
            ctype = response.headers.get("content-type", "")
            all_responses.append((response.url, response.status, ctype))
            if "json" in ctype or "javascript" in ctype:
                async def grab():
                    try:
                        json_bodies[response.url] = await response.text()
                    except Exception:
                        pass
                asyncio.ensure_future(grab())

        page.on("response", on_response)

        try:
            resp = await page.goto(
                "https://www.aliexpress.com/wholesale?SearchText=phone+case",
                wait_until="domcontentloaded",
                timeout=45000,
            )
            print("goto status:", resp.status if resp else None)
        except Exception as e:
            print("goto error:", e)
            await browser.close()
            return

        try:
            await page.wait_for_selector("img[alt='Search by image']", timeout=20000)
            print("Found image search icon")
        except Exception as e:
            print("Could not find image search icon:", e)
            await page.screenshot(path=str(OUT_DIR / "ae_1_no_icon.png"))
            await browser.close()
            return

        await page.click("img[alt='Search by image']")
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT_DIR / "ae_2_after_click.png"))

        try:
            file_input = page.locator("input[type=file]").first
            await file_input.set_input_files(str(IMAGE_PATH), timeout=10000)
            print("set_input_files succeeded")
        except Exception as e:
            print("set_input_files failed:", e)
            await browser.close()
            return

        for i in range(6):
            await page.wait_for_timeout(2000)
            print(f"t+{(i+1)*2}s url:", page.url)

        await page.screenshot(path=str(OUT_DIR / "ae_3_after_upload.png"))
        html = await page.content()
        (OUT_DIR / "ae_after_upload.html").write_text(html)
        print("HTML length:", len(html))
        print("Total responses observed:", len(all_responses))
        for url, status, ctype in all_responses:
            print(status, ctype, url[:220])

        print("\nJSON/JS bodies captured:", len(json_bodies))
        for url, body in json_bodies.items():
            if "aidcgroup" in url or ("productId" in body or "itemId" in body):
                fname = "ae_body_" + str(abs(hash(url)) % 10000) + ".json"
                (OUT_DIR / fname).write_text(body)
                print("saved", fname, "<-", url[:180], "len", len(body))

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
