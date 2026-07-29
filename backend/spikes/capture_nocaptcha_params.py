import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.async_api import async_playwright  # noqa: E402

from app.config import settings  # noqa: E402

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
        captured = []

        def on_request(req):
            if "nocaptcha" in req.url.lower() or "punish" in req.url.lower() or "baxia" in req.url.lower():
                captured.append(("REQ", req.method, req.url))

        def on_response_sync(resp):
            if "nocaptcha" in resp.url.lower():
                captured.append(("RESP", str(resp.status), resp.url))

        page.on("request", on_request)
        page.on("response", on_response_sync)

        title = ""
        for attempt in range(6):
            await page.goto(
                "https://www.alibaba.com/picture/search.htm", wait_until="domcontentloaded", timeout=45000
            )
            await page.wait_for_timeout(3000)
            title = await page.title()
            print(f"attempt {attempt+1}: title={title!r}")
            if "Captcha" in title:
                break
        else:
            print("Never hit captcha after 6 attempts")
            await browser.close()
            return

        await page.wait_for_timeout(3000)
        html = await page.content()
        (OUT_DIR / "nocaptcha_live.html").write_text(html)
        await page.screenshot(path=str(OUT_DIR / "nocaptcha_live.png"))

        print(f"\nCaptured {len(captured)} nocaptcha/punish/baxia-related network events:")
        for kind, a, url in captured:
            print(kind, a, url[:300])

        # Also dump window._config_ if present, since that's where NCTOKENSTR etc live
        config = await page.evaluate("() => JSON.stringify(window._config_ || {})")
        print("\nwindow._config_:", config[:2000])

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
