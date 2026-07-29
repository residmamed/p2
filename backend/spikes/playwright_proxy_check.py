import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402

from app.config import settings  # noqa: E402


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            proxy={
                "server": "https://api.zyte.com:8014",
                "username": settings.zyte_api_key,
                "password": "",
            },
            headless=True,
            args=["--ignore-certificate-errors"],
        )
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        page.on("console", lambda msg: print("CONSOLE:", msg.type, msg.text))
        page.on("requestfailed", lambda req: print("REQ FAILED:", req.url, req.failure))

        for url in ["http://httpbin.org/ip", "https://httpbin.org/ip", "https://www.alibaba.com/"]:
            print(f"\n--- Trying {url} ---")
            try:
                resp = await page.goto(url, wait_until="load", timeout=30000)
                print("status:", resp.status if resp else None)
                body = await page.content()
                print("body snippet:", body[:300])
            except Exception as e:
                print("ERROR:", repr(e))

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
