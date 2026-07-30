"""Oxylabs Web Scraper API — the enrichment transport for Lens Sourcing.

Step 2 of `app/lens_suppliers.py`. Google Lens hands us a product URL and
nothing much else; this turns that URL into the page behind it so a supplier
name, a price and an MOQ can be read off it. Plain REST, one POST, no browser —
which is the entire reason this pipeline exists next to the Browserbase one.

**Integration method.** The `realtime` endpoint holds the HTTPS connection open
from submission until the result comes back, so there is no job id to poll and
no second round trip. `push-pull` would be the right choice for a batch of
thousands; for six URLs inside a request the user is waiting on, an extra poll
loop is pure added latency.

    POST https://realtime.oxylabs.io/v1/queries
    basic auth: the dashboard's *API user* credentials, not the dashboard login
    {"source": ..., "url": ...}  ->  {"results": [{"content": ..., "status_code": ...}]}

**Source names**, confirmed against the docs on 2026-07-29 (they do move — the
per-target pages are at developers.oxylabs.io/api-targets/e-commerce/<site>):

    alibaba          any Alibaba URL. Returns HTML.
    alibaba_product  an Alibaba product *id*, not a URL. Returns HTML.
    alibaba_search   a search term. Returns HTML.
    universal        any public site. The only option for 1688 and Taobao,
                     which have no dedicated target.

`alibaba` is the one used here: Lens gives us a URL, and `alibaba_product` would
mean first extracting the numeric id and then losing the ability to fall back to
`universal` if that id parse is wrong. All three return HTML, not parsed JSON —
Alibaba is not one of the domains `parse: true` supports — so the structured
schema is built by `parsing/marketplace_product.py` rather than by Oxylabs.

Failures are typed rather than flattened, because they mean different things to
the caller: bad credentials are an operator's problem that must be surfaced
loudly, while one slow product page is an ordinary partial result.
"""
import asyncio
from urllib.parse import urlparse

import httpx

from .config import settings

OXYLABS_REALTIME_URL = "https://realtime.oxylabs.io/v1/queries"

# Per the brief: one hung page must not hold the whole request. Measured against
# live Alibaba product pages on 2026-07-29 — six fetched concurrently returned
# 410-454KB each in 2.3s, 2.3s, 2.5s, 2.8s, 3.4s and 4.9s — so eight seconds
# clears the observed spread with headroom and still bounds the tail.
PER_URL_TIMEOUT = 8.0

# Oxylabs' own gateway, not the target site: a 504 arrives in milliseconds and
# the retry usually succeeds. Observed turning 3-of-4 into 4-of-4 on an
# otherwise identical batch. Deliberately excludes timeouts — those have already
# spent the full per-URL budget, and spending it twice is how one slow page
# doubles the latency of the whole request instead of degrading one row.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 1
RETRY_BACKOFF_SECONDS = 0.4

# Which Oxylabs source serves each marketplace. 1688 and Taobao have no
# dedicated target, so they go through `universal`, which is what the docs
# prescribe for "generic targets which do not have a dedicated source".
SOURCE_FOR_SITE = {
    "alibaba": "alibaba",
    "1688": "universal",
    "taobao": "universal",
}

# Alibaba localises its prices to the exit IP, and Oxylabs rotates exits
# worldwide, so the same listing comes back in a different currency on every
# other request. Measured 2026-07-29, six fetches of one product URL:
#
#     no geo_location   R 28,52 · 82.47 TL · $1.67 · R 28,52 · R 28,52 · $1.67
#     "United States"   $1.67 · $1.67 · $1.67 · $1.67 · $1.67 · $1.67
#
# Each of those is internally consistent and none is *wrong*, but a results
# table that quotes one supplier in rand and the next in dollars cannot be used
# to compare suppliers, which is the entire job. Pinning the exit fixes it at
# the source, which is much better than converting downstream — there is no
# exchange rate in this codebase and an invented one would put a wrong number on
# a supplier quote.
#
# 1688 and Taobao are deliberately absent. They are domestic Chinese sites that
# quote CNY natively; a US exit would be the wrong hint there and risks the
# reachability that currently works.
GEO_FOR_SITE: dict[str, str] = {"alibaba": "United States"}


class OxylabsError(Exception):
    """Enrichment failed for one URL. The caller keeps the row on its Lens data."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OxylabsAuthError(OxylabsError):
    """The credentials were rejected. Distinct from OxylabsError on purpose:
    every URL in the batch will fail the same way, and the answer is not
    "degrade gracefully" but "tell whoever deployed this"."""


def is_configured() -> bool:
    return bool(settings.oxylabs_username and settings.oxylabs_password)


def site_for_url(url: str) -> str | None:
    """Which marketplace a URL belongs to, by hostname suffix.

    Suffix matching, not substring: `aliexpress.com` contains neither
    `.alibaba.com` nor a match for it, but a naive `"alibaba" in url` test would
    also catch `alibaba.example.com` and any URL with the word in a query
    string. Getting this wrong sends AliExpress consumer listings to an Alibaba
    B2B parser and reports the results as supplier quotes.
    """
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    for site, domain in (("alibaba", "alibaba.com"), ("1688", "1688.com"), ("taobao", "taobao.com")):
        if host == domain or host.endswith("." + domain):
            return site
    return None


class OxylabsClient:
    """One client per request; `scrape` is safe to call concurrently on it."""

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        timeout: float = PER_URL_TIMEOUT,
        render: bool | None = None,
    ):
        self._auth = (
            username or settings.oxylabs_username,
            password or settings.oxylabs_password,
        )
        self._timeout = timeout
        self._render = settings.oxylabs_render if render is None else render

    def is_configured(self) -> bool:
        return bool(self._auth[0] and self._auth[1])

    def _payload(self, url: str, site: str | None) -> dict:
        payload: dict = {
            "source": SOURCE_FOR_SITE.get(site or "", "universal"),
            "url": url,
        }
        if self._render:
            payload["render"] = "html"
        geo = GEO_FOR_SITE.get(site or "")
        if geo:
            payload["geo_location"] = geo
        return payload

    async def scrape(self, url: str, site: str | None = None, client: httpx.AsyncClient | None = None) -> str:
        """Fetch one product page and return its HTML.

        Raises rather than returning "" on failure. An empty string would be
        indistinguishable from a page that genuinely rendered nothing, and the
        caller's whole fallback decision — keep the Lens data for this row, or
        trust the enrichment — turns on knowing which happened. Same rule
        ZyteClient.extract_product_list follows, for the same reason.
        """
        if not self.is_configured():
            raise OxylabsAuthError(
                "Oxylabs is not configured — set OXYLABS_USERNAME and "
                "OXYLABS_PASSWORD in backend/.env."
            )

        owns_client = client is None
        client = client or httpx.AsyncClient(timeout=self._timeout)
        try:
            for attempt in range(MAX_RETRIES + 1):
                try:
                    response = await client.post(
                        OXYLABS_REALTIME_URL,
                        json=self._payload(url, site),
                        auth=self._auth,
                        timeout=self._timeout,
                    )
                except httpx.TimeoutException as e:
                    raise OxylabsError(f"Oxylabs timed out after {self._timeout:.0f}s.") from e
                except httpx.HTTPError as e:
                    raise OxylabsError(f"Oxylabs request failed ({type(e).__name__}).") from e

                if response.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS)
                    continue
                break
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code in (401, 403):
            raise OxylabsAuthError(
                f"Oxylabs rejected the credentials (HTTP {response.status_code}). "
                "Check OXYLABS_USERNAME / OXYLABS_PASSWORD — these are the API user "
                "credentials from the dashboard, not the dashboard login."
            )
        if response.status_code != 200:
            raise OxylabsError(
                f"Oxylabs returned HTTP {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        try:
            body = response.json()
        except ValueError as e:
            raise OxylabsError("Oxylabs returned a non-JSON body.") from e

        results = body.get("results")
        if not isinstance(results, list) or not results:
            raise OxylabsError("Oxylabs returned no results for this URL.")

        first = results[0] if isinstance(results[0], dict) else {}
        # The target's own status, which is separate from Oxylabs' 200 for
        # having done the job. A 404 or a challenge page arrives as a successful
        # scrape of a useless page, and parsing it would invent a supplier.
        page_status = first.get("status_code")
        if isinstance(page_status, int) and page_status >= 400:
            raise OxylabsError(
                f"The product page answered HTTP {page_status}.", status_code=page_status
            )

        content = first.get("content")
        if isinstance(content, dict):
            # `parse: true` territory. Not requested here (Alibaba has no parsed
            # schema), but a future dedicated source could start returning one,
            # and silently handing a dict to an HTML parser would look like an
            # unparseable page rather than a changed contract.
            raise OxylabsError(
                "Oxylabs returned parsed JSON where HTML was expected — "
                "the source's contract has changed."
            )
        if not isinstance(content, str) or not content.strip():
            raise OxylabsError("Oxylabs returned an empty page.")
        return content


async def scrape_many(
    urls: list[str],
    client_factory: OxylabsClient | None = None,
    concurrency: int = 6,
) -> list[tuple[str, str | None, OxylabsError | None]]:
    """Fetch a batch concurrently, capped. Returns (url, html, error) per URL,
    in the input order — never raises, so one bad URL costs one row.

    The cap exists because Oxylabs bills and rate-limits per request and because
    firing forty product pages at once is how a pipeline that "doesn't
    serialize" turns into a pipeline that gets 429'd.
    """
    oxy = client_factory or OxylabsClient()
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(timeout=oxy._timeout) as http:

        async def _one(url: str):
            async with semaphore:
                try:
                    html = await oxy.scrape(url, site_for_url(url), client=http)
                except OxylabsError as e:
                    return url, None, e
                except Exception as e:  # noqa: BLE001 - one dead page must not sink the batch
                    return url, None, OxylabsError(f"Unexpected enrichment error: {e}")
                return url, html, None

        return list(await asyncio.gather(*(_one(u) for u in urls)))
