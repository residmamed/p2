import os
import tempfile
from urllib.parse import quote

from playwright.async_api import async_playwright

from .. import credentials
from ..parsing.aliexpress_parser import parse_search_results
from ..zyte_client import ZyteClient
from .base import Scraper

SEARCH_URL = "https://www.aliexpress.com/wholesale?SearchText={query}"
IMAGE_SEARCH_ENTRY_URL = "https://www.aliexpress.com/wholesale?SearchText=search"
MAX_TEXT_RETRIES = 3
MAX_IMAGE_UPLOAD_ATTEMPTS = 3
MAX_IMAGE_FETCH_RETRIES = 2


class AliExpressScraper(Scraper):
    def __init__(self, zyte_client: ZyteClient | None = None):
        self._zyte = zyte_client or ZyteClient()

    async def search_by_text(self, query: str, page: int = 1):
        url = SEARCH_URL.format(query=quote(query))
        if page > 1:
            url += f"&page={page}"

        warnings: list[str] = []
        for attempt in range(1, MAX_TEXT_RETRIES + 1):
            result = await self._zyte.extract(url, browser_html=True)
            html_text = result.get("browserHtml", "")
            products = parse_search_results(html_text)
            if products:
                return products, warnings
            warnings.append(f"AliExpress challenged or returned nothing on attempt {attempt}/{MAX_TEXT_RETRIES}")

        warnings.append("AliExpress blocked this search after retries — showing no results. Try again shortly.")
        return [], warnings

    async def search_by_image(self, image_bytes: bytes, content_type: str):
        warnings: list[str] = []

        results_url = None
        for attempt in range(1, MAX_IMAGE_UPLOAD_ATTEMPTS + 1):
            results_url = await self._upload_image_and_get_results_url(image_bytes)
            if results_url:
                break
            warnings.append(f"AliExpress image upload attempt {attempt}/{MAX_IMAGE_UPLOAD_ATTEMPTS} was blocked or timed out")

        if not results_url:
            warnings.append(
                "AliExpress's image search could not be completed after retries. "
                "This feature is best-effort due to AliExpress's anti-bot protections — try again shortly."
            )
            return [], warnings

        for attempt in range(1, MAX_IMAGE_FETCH_RETRIES + 1):
            result = await self._zyte.extract(results_url, browser_html=True)
            html_text = result.get("browserHtml", "")
            products = parse_search_results(html_text)
            if products:
                return products, warnings
            warnings.append(f"No image-search results parsed on fetch attempt {attempt}/{MAX_IMAGE_FETCH_RETRIES}")

        warnings.append("AliExpress returned an image search page but no results could be parsed.")
        return [], warnings

    async def _upload_image_and_get_results_url(self, image_bytes: bytes) -> str | None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                proxy={
                    "server": "https://api.zyte.com:8014",
                    "username": credentials.ZYTE.next() or "",
                    "password": "",
                },
                headless=True,
                args=["--ignore-certificate-errors", "--disable-blink-features=AutomationControlled"],
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
                    await page.goto(IMAGE_SEARCH_ENTRY_URL, wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    return None

                try:
                    await page.wait_for_selector("img[alt='Search by image']", timeout=20000)
                except Exception:
                    return None

                try:
                    await page.click("img[alt='Search by image']")
                except Exception:
                    return None

                await page.wait_for_timeout(1500)

                fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
                with os.fdopen(fd, "wb") as f:
                    f.write(image_bytes)
                try:
                    file_input = page.locator("input[type=file]").first
                    # The temp file must stay on disk after this call returns — the page
                    # reads it asynchronously as part of its own upload flow.
                    await file_input.set_input_files(tmp_path, timeout=10000)

                    start_url = page.url
                    for _ in range(6):
                        await page.wait_for_timeout(2000)
                        if page.url != start_url and "isNewImageSearch=y" in page.url:
                            return page.url

                    return None
                except Exception:
                    return None
                finally:
                    os.unlink(tmp_path)
            finally:
                await browser.close()
