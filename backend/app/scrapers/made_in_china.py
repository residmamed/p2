import asyncio
import base64
import os
import re
import tempfile
from urllib.parse import quote

from playwright.async_api import async_playwright

from ..config import settings
from ..parsing.made_in_china_parser import parse_search_results
from ..zyte_client import ZyteClient
from .base import Scraper

SEARCH_URL = (
    "https://www.made-in-china.com/productdirectory.do?subaction=hunt&style=b&mode=and&code=0"
    "&comProvince=nolimit&order=0&isOpenCorrection=1&org=top&searchType=0&word={query}&page={page}"
)
HOME_URL = "https://www.made-in-china.com/"
IMG_SEARCH_URL_RE = re.compile(r"/img-search/[A-Za-z0-9]+\.html")
MAX_TEXT_RETRIES = 3
MAX_IMAGE_UPLOAD_ATTEMPTS = 2
MAX_IMAGE_FETCH_RETRIES = 2


class MadeInChinaScraper(Scraper):
    def __init__(self, zyte_client: ZyteClient | None = None):
        self._zyte = zyte_client or ZyteClient()

    async def search_by_text(self, query: str, page: int = 1):
        url = SEARCH_URL.format(query=quote(query), page=page)

        warnings: list[str] = []
        for attempt in range(1, MAX_TEXT_RETRIES + 1):
            try:
                result = await self._zyte.extract(url, browser_html=False, http_response_body=True)
            except Exception as e:
                if "429" in str(e):
                    warnings.append(f"Made-in-China rate-limited attempt {attempt}/{MAX_TEXT_RETRIES}, backing off")
                    await asyncio.sleep(30 * attempt)
                    continue
                raise

            html_text = self._decode_body(result)
            products = parse_search_results(html_text)
            if products:
                return products, warnings
            warnings.append(f"No products parsed on attempt {attempt}/{MAX_TEXT_RETRIES}")
            await asyncio.sleep(5)

        warnings.append("Made-in-China returned no results after retries. Try again shortly.")
        return [], warnings

    async def search_by_image(self, image_bytes: bytes, content_type: str):
        warnings: list[str] = []

        results_url = None
        for attempt in range(1, MAX_IMAGE_UPLOAD_ATTEMPTS + 1):
            results_url = await self._upload_image_and_get_results_url(image_bytes)
            if results_url:
                break
            warnings.append(f"Made-in-China image upload attempt {attempt}/{MAX_IMAGE_UPLOAD_ATTEMPTS} failed")

        if not results_url:
            warnings.append("Made-in-China's image search could not be completed after retries. Try again shortly.")
            return [], warnings

        for attempt in range(1, MAX_IMAGE_FETCH_RETRIES + 1):
            result = await self._zyte.extract(results_url, browser_html=False, http_response_body=True)
            html_text = self._decode_body(result)
            products = parse_search_results(html_text)
            if products:
                return products, warnings
            warnings.append(f"No image-search results parsed on fetch attempt {attempt}/{MAX_IMAGE_FETCH_RETRIES}")
            await asyncio.sleep(3)

        warnings.append("Made-in-China returned an image search page but no results could be parsed.")
        return [], warnings

    @staticmethod
    def _decode_body(result: dict) -> str:
        body_b64 = result.get("httpResponseBody", "")
        if not body_b64:
            return ""
        return base64.b64decode(body_b64).decode("utf-8", errors="replace")

    async def _upload_image_and_get_results_url(self, image_bytes: bytes) -> str | None:
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
                page = await context.new_page()
                tmp_path: str | None = None
                try:
                    try:
                        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
                    except Exception:
                        return None

                    try:
                        await page.wait_for_selector("a.upload-img-camera", timeout=15000)
                    except Exception:
                        return None

                    try:
                        await page.keyboard.press("Escape")
                        mask = page.locator(".campaign-pop-mask")
                        if await mask.count() > 0:
                            close_btn = page.locator(
                                ".campaign-pop-mask [class*=close], .campaign-pop-mask [class*=Close]"
                            )
                            if await close_btn.count() > 0:
                                await close_btn.first.click(timeout=3000)
                            else:
                                await mask.evaluate("el => el.remove()")
                    except Exception:
                        pass

                    await page.wait_for_timeout(500)
                    try:
                        await page.click("a.upload-img-camera", force=True)
                    except Exception:
                        return None
                    await page.wait_for_timeout(1500)

                    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
                    with os.fdopen(fd, "wb") as f:
                        f.write(image_bytes)
                    try:
                        file_input = page.locator("input[type=file]").first
                        # Note: the temp file must stay on disk until well after this call —
                        # the page reads it asynchronously as part of its own upload flow.
                        await file_input.set_input_files(tmp_path, timeout=10000)
                    except Exception:
                        return None

                    for _ in range(6):
                        await page.wait_for_timeout(2000)
                        if IMG_SEARCH_URL_RE.search(page.url):
                            return page.url.replace("http://", "https://", 1)

                    return None
                finally:
                    if tmp_path:
                        os.unlink(tmp_path)
            finally:
                await browser.close()
