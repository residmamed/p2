import asyncio
import base64

import httpx

from . import credentials

ZYTE_EXTRACT_URL = "https://api.zyte.com/v1/extract"
# 520 is Zyte's "website ban" — the target site refused *that* attempt. Retrying
# gets a different exit IP and frequently succeeds, so it belongs here with the
# transient statuses. Leaving it out meant a single ban silently downgraded a
# site from its real parser to the generic productList fallback, which returns
# lazy-load placeholder images and no seller — data that looks fine and isn't.
RETRYABLE_STATUS = {429, 500, 502, 503, 504, 520}
# productList runs Zyte's own browser rendering + AI extraction, which on
# Amazon/Walmart routinely exceeds the 90s used for plain fetches — more so when
# several sites are fanned out at once. Measured: ~45s warm, >90s under load.
PRODUCT_LIST_TIMEOUT = 180.0


class ZyteError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ZyteClient:
    def __init__(self, api_key: str | None = None, timeout: float = 90.0):
        # One account per client rather than per call. A client is built per
        # request and then used for a burst of concurrent extractions
        # (PRODUCT_PAGE_CONCURRENCY is 24), so this still spreads the load —
        # and it keeps one request's burst on one account, which is what makes
        # a rate-limit response attributable to an account rather than a mystery.
        self._auth = (api_key or credentials.ZYTE.next() or "", "")
        self._timeout = timeout

    async def extract(
        self,
        url: str,
        *,
        browser_html: bool = True,
        http_response_body: bool = False,
        actions: list[dict] | None = None,
        screenshot: bool = False,
        max_retries: int = 2,
    ) -> dict:
        """Call Zyte API's /v1/extract endpoint and return the raw JSON response."""
        payload: dict = {"url": url}
        if browser_html:
            payload["browserHtml"] = True
        if http_response_body:
            payload["httpResponseBody"] = True
        if actions:
            payload["actions"] = actions
        if screenshot:
            payload["screenshot"] = True

        attempt = 0
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            while True:
                attempt += 1
                response = await client.post(
                    ZYTE_EXTRACT_URL, json=payload, auth=self._auth
                )
                if response.status_code == 200:
                    return response.json()

                if (
                    response.status_code in RETRYABLE_STATUS
                    and attempt <= max_retries
                ):
                    await asyncio.sleep(min(2**attempt, 10))
                    continue

                raise ZyteError(
                    f"Zyte API request failed ({response.status_code}): {response.text[:500]}",
                    status_code=response.status_code,
                )

    @staticmethod
    def image_to_data_url(image_bytes: bytes, content_type: str) -> str:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    async def extract_product_list(
        self, url: str, *, max_retries: int = 2, timeout: float | None = None
    ) -> list[dict]:
        """Use Zyte API's automatic (AI-based) product-list extraction to pull a
        list of products off an arbitrary search/listing page — name, price,
        image, url — without a site-specific parser. This is what makes Product
        Search work uniformly across sites we've never written a parser for.
        Zyte manages rendering + ban handling itself. List order reflects the
        page's own order.

        Raises ZyteError on failure rather than returning []. An empty list must
        mean "this page genuinely had no products" — when a timeout could also
        produce [], callers report "No products returned" for what was really a
        network failure, and the user reads a truthful-looking empty grid. That
        exact confusion cost a debugging session; don't reintroduce it.
        """
        payload = {"url": url, "productList": True}
        attempt = 0
        # Browser-rendered retail pages (Amazon, Walmart) regularly run past the
        # default 90s, especially with several sites fanned out concurrently.
        async with httpx.AsyncClient(timeout=timeout or PRODUCT_LIST_TIMEOUT) as client:
            while True:
                attempt += 1
                try:
                    response = await client.post(ZYTE_EXTRACT_URL, json=payload, auth=self._auth)
                except httpx.HTTPError as e:
                    if attempt <= max_retries:
                        await asyncio.sleep(min(2**attempt, 10))
                        continue
                    raise ZyteError(
                        f"Zyte productList request failed after {attempt} attempts "
                        f"({type(e).__name__}) — the site may be slow or unreachable."
                    ) from e
                if response.status_code == 200:
                    return response.json().get("productList", {}).get("products", []) or []
                if response.status_code in RETRYABLE_STATUS and attempt <= max_retries:
                    await asyncio.sleep(min(2**attempt, 10))
                    continue
                raise ZyteError(
                    f"Zyte productList request failed ({response.status_code}): {response.text[:300]}",
                    status_code=response.status_code,
                )

    async def extract_with_product(self, url: str, *, timeout: float | None = None) -> dict:
        """Structured product extraction AND the rendered HTML, in one request.

        Zyte bills and renders per request, not per output, so asking for both
        together costs one page fetch instead of two. Both halves are needed to
        identify a supplier and neither is sufficient alone: `product.brand`
        names the company, while only the HTML carries the link to its own
        company page — which is the handle supplier_profile needs to find any
        published contact. See supplier_resolve.

        Raises ZyteError rather than returning {} so a fetch failure can't be
        read as "this page had no supplier".
        """
        payload = {"url": url, "product": True, "browserHtml": True}
        attempt = 0
        async with httpx.AsyncClient(timeout=timeout or PRODUCT_LIST_TIMEOUT) as client:
            while True:
                attempt += 1
                try:
                    response = await client.post(ZYTE_EXTRACT_URL, json=payload, auth=self._auth)
                except httpx.HTTPError as e:
                    if attempt <= 2:
                        await asyncio.sleep(min(2**attempt, 10))
                        continue
                    raise ZyteError(
                        f"Zyte product page request failed after {attempt} attempts "
                        f"({type(e).__name__})."
                    ) from e
                if response.status_code == 200:
                    return response.json()
                if response.status_code in RETRYABLE_STATUS and attempt <= 2:
                    await asyncio.sleep(min(2**attempt, 10))
                    continue
                raise ZyteError(
                    f"Zyte product page request failed ({response.status_code}): "
                    f"{response.text[:300]}",
                    status_code=response.status_code,
                )

    async def extract_product(self, url: str) -> dict | None:
        """Use Zyte API's automatic (AI-based) product extraction to pull
        name/price/image data from an arbitrary product page, without needing
        a site-specific parser. Returns None on any failure — this enriches
        best-effort results, so a bad URL shouldn't blow up the whole batch."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    ZYTE_EXTRACT_URL, json={"url": url, "product": True}, auth=self._auth
                )
            except httpx.HTTPError:
                return None
        if response.status_code != 200:
            return None
        return response.json().get("product")
