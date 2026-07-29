import os
import tempfile
from urllib.parse import quote

from playwright.async_api import async_playwright

from ..config import settings
from ..models import Product
from ..parsing.alibaba_parser import parse_search_results
from ..zyte_client import ZyteClient
from .base import Scraper

SEARCH_URL = "https://www.alibaba.com/trade/search?SearchText={query}"
IMAGE_SEARCH_URL = "https://www.alibaba.com/picture/search.htm"
CAPTCHA_MARKER = "Captcha Interception"
MAX_TEXT_RETRIES = 3
MAX_IMAGE_RETRIES = 2


class AlibabaScraper(Scraper):
    def __init__(self, zyte_client: ZyteClient | None = None):
        self._zyte = zyte_client or ZyteClient()

    async def search_by_text(self, query: str, page: int = 1) -> tuple[list[Product], list[str]]:
        url = SEARCH_URL.format(query=quote(query))
        if page > 1:
            url += f"&page={page}"

        warnings: list[str] = []
        for attempt in range(1, MAX_TEXT_RETRIES + 1):
            result = await self._zyte.extract(url, browser_html=True)
            html_text = result.get("browserHtml", "")
            if CAPTCHA_MARKER in html_text[:2000]:
                warnings.append(f"Alibaba challenged attempt {attempt}/{MAX_TEXT_RETRIES}")
                continue
            products = parse_search_results(html_text)
            if products:
                return products, warnings
            warnings.append(f"No products parsed on attempt {attempt}/{MAX_TEXT_RETRIES}")

        warnings.append("Alibaba blocked this search after retries — showing no results. Try again shortly.")
        return [], warnings

    async def search_by_image(self, image_bytes: bytes, content_type: str) -> tuple[list[Product], list[str]]:
        warnings: list[str] = []
        for attempt in range(1, MAX_IMAGE_RETRIES + 1):
            products, blocked = await self._try_image_search(image_bytes)
            if products:
                return products, warnings
            if blocked:
                warnings.append(f"Alibaba challenged image search attempt {attempt}/{MAX_IMAGE_RETRIES}")
            else:
                warnings.append(f"Image search attempt {attempt}/{MAX_IMAGE_RETRIES} returned no results")

        warnings.append(
            "Alibaba's image search could not be completed after retries. "
            "This feature is best-effort due to Alibaba's anti-bot protections — try again shortly."
        )
        return [], warnings

    async def _try_image_search(self, image_bytes: bytes) -> tuple[list[Product], bool]:
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
                ],
            )
            try:
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
                    await page.goto(IMAGE_SEARCH_URL, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    return [], True

                try:
                    await page.wait_for_selector(
                        "[data-search='switch-image-upload']", timeout=15000
                    )
                except Exception:
                    title = await page.title()
                    return [], CAPTCHA_MARKER in title

                await page.click("[data-search='switch-image-upload']")
                await page.wait_for_timeout(1500)

                fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
                with os.fdopen(fd, "wb") as f:
                    f.write(image_bytes)
                try:
                    file_input = page.locator("input[type=file]").first
                    # The temp file must stay on disk after this call returns — the page
                    # reads it asynchronously as part of its own upload flow.
                    await file_input.set_input_files(tmp_path, timeout=10000)

                    await page.wait_for_timeout(6000)
                    html_text = await page.content()

                    title = await page.title()
                    if CAPTCHA_MARKER in title:
                        return [], True

                    products = parse_search_results(html_text)
                    return products, False
                finally:
                    os.unlink(tmp_path)
            finally:
                await browser.close()
