"""Browserbase cloud-browser sessions for the parts of sourcing that genuinely
need a real browser: driving the supplier sites' image-upload widgets.

Why this exists at all: none of Alibaba / 1688 / AliExpress / Made-in-China can
be image-searched through Zyte's managed browser, because Zyte's `actions` API
has no file-upload action and JS-dispatched synthetic file events get rejected
(`isTrusted: false`). A real browser calling the CDP file-input API produces
trusted native events. That was already true when this ran on local Playwright
(see scrapers/alibaba.py) — Browserbase just moves it off this machine, so the
backend no longer ships a Chromium, and concurrent searches stop fighting over
one local browser.

Everything *after* the upload still goes through Zyte: the browser's only job is
to turn an image into a results URL. See scrapers/image_discovery.py.

File uploads on a remote browser need care. `set_input_files(local_path)` refers
to a path on the *browser's* filesystem, not ours, so we push the bytes through
Browserbase's session-uploads endpoint (which lands them at
`/tmp/.uploads/<name>` inside the session) and then attach them with the raw CDP
`DOM.setFileInputFiles` command. That is the documented remote-upload path and
the only one that produces a trusted event without a local file.
"""
import asyncio
import contextlib
import threading
from dataclasses import dataclass

import httpx
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .config import settings

SESSIONS_URL = "https://api.browserbase.com/v1/sessions"
CONNECT_URL = "wss://connect.browserbase.com"
REMOTE_UPLOAD_DIR = "/tmp/.uploads"
SESSION_TIMEOUT_SECONDS = 180


class BrowserbaseError(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.browserbase_api_key and settings.browserbase_project_id)


@dataclass
class RemoteBrowser:
    """A live cloud browser plus the handles callers actually use. `session_id`
    is kept so failures can be replayed from Browserbase's session recording —
    that replay is the fastest way to fix a broken site recipe."""

    session_id: str
    browser: Browser
    context: BrowserContext
    page: Page


async def _create_session(client: httpx.AsyncClient) -> str:
    browser_settings: dict = {"solveCaptchas": True}
    if settings.browserbase_advanced_stealth:
        browser_settings["advancedStealth"] = True

    response = await client.post(
        SESSIONS_URL,
        headers={"X-BB-API-Key": settings.browserbase_api_key},
        json={
            "projectId": settings.browserbase_project_id,
            "proxies": settings.browserbase_proxies,
            "browserSettings": browser_settings,
            "timeout": SESSION_TIMEOUT_SECONDS,
        },
    )
    if response.status_code not in (200, 201):
        raise BrowserbaseError(
            f"Browserbase session create failed ({response.status_code}): {response.text[:300]}"
        )
    session_id = response.json().get("id")
    if not session_id:
        raise BrowserbaseError("Browserbase session create returned no id")
    return session_id


@contextlib.asynccontextmanager
async def remote_browser():
    """Yield a RemoteBrowser, tearing the cloud session down afterwards.

    Browserbase sessions are billed by the second and have their own idle
    timeout, so the context manager closes eagerly rather than relying on it.
    """
    if not is_configured():
        raise BrowserbaseError(
            "Browserbase is not configured — set BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID"
        )

    async with httpx.AsyncClient(timeout=60.0) as client:
        session_id = await _create_session(client)

    pw = await async_playwright().start()
    browser = None
    try:
        connect_url = (
            f"{CONNECT_URL}?apiKey={settings.browserbase_api_key}&sessionId={session_id}"
        )
        browser = await pw.chromium.connect_over_cdp(connect_url)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        yield RemoteBrowser(session_id=session_id, browser=browser, context=context, page=page)
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.close()
        with contextlib.suppress(Exception):
            await pw.stop()


async def upload_to_session(session_id: str, image_bytes: bytes, filename: str) -> str:
    """Push image bytes into the running session and return the path they live
    at inside the remote browser. Pairs with attach_file() below."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{SESSIONS_URL}/{session_id}/uploads",
            headers={"X-BB-API-Key": settings.browserbase_api_key},
            files={"file": (filename, image_bytes, "application/octet-stream")},
        )
    if response.status_code not in (200, 201):
        raise BrowserbaseError(
            f"Browserbase upload failed ({response.status_code}): {response.text[:300]}"
        )
    return f"{REMOTE_UPLOAD_DIR}/{filename}"


async def attach_file(rb: RemoteBrowser, selector: str, remote_path: str) -> None:
    """Attach an already-uploaded remote file to a file input via raw CDP.

    Playwright's set_input_files() would look for the path on *our* filesystem;
    DOM.setFileInputFiles resolves it on the browser's, which is where the
    session-uploads endpoint put it.
    """
    cdp = await rb.context.new_cdp_session(rb.page)
    try:
        root = await cdp.send("DOM.getDocument")
        node = await cdp.send(
            "DOM.querySelector",
            {"nodeId": root["root"]["nodeId"], "selector": selector},
        )
        if not node.get("nodeId"):
            raise BrowserbaseError(f"No file input matched {selector!r}")
        await cdp.send(
            "DOM.setFileInputFiles",
            {"files": [remote_path], "nodeId": node["nodeId"]},
        )
    finally:
        with contextlib.suppress(Exception):
            await cdp.detach()


async def wait_for_url(page: Page, matcher, timeout_ms: int = 30_000, poll_ms: int = 1000) -> str | None:
    """Poll page.url until `matcher` accepts it.

    Deliberately polling rather than page.wait_for_url(): these upload flows
    navigate via in-page JS after an XHR completes, and several of them bounce
    through one or two interstitial URLs first, which regex-based wait_for_url
    handles badly.
    """
    waited = 0
    while waited < timeout_ms:
        await page.wait_for_timeout(poll_ms)
        waited += poll_ms
        if matcher(page.url):
            return page.url
    return None


# Process-wide ceiling on live cloud browsers.
#
# `fetch_pages` bounds one REQUEST, which is not the same thing and was not
# enough: the frontend fires every product's supplier lookup at once, so a
# 40-product grid becomes 40 simultaneous requests, each entitled to its own 25
# browsers — about a thousand sessions asked for against a plan that allows 25.
# Over the cap Browserbase fails the create outright rather than queueing, so
# the visible symptom is most of the grid coming back empty, worst exactly when
# the grid is biggest.
#
# Lazily built because a Semaphore binds to the running loop at construction.
_session_limit: asyncio.Semaphore | None = None
_session_limit_lock = threading.Lock()


def _limiter() -> asyncio.Semaphore:
    global _session_limit
    if _session_limit is None:
        with _session_limit_lock:
            if _session_limit is None:
                _session_limit = asyncio.Semaphore(settings.browserbase_page_concurrency)
    return _session_limit


# Everything a product page loads that we will never read. The parser works on
# HTML — prices, ratings and the supplier blob are all text — so pictures,
# fonts, stylesheets and video are pure latency on pages that carry dozens of
# them. Measured below; this is the single biggest per-page saving available,
# and unlike shortening the settle it cannot cost us a field.
_BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet"}


async def _block_heavy_assets(page: Page) -> None:
    async def route(route_obj):
        if route_obj.request.resource_type in _BLOCKED_RESOURCES:
            await route_obj.abort()
        else:
            await route_obj.continue_()

    await page.route("**/*", route)


async def fetch_page(url: str, timeout_ms: int = 45_000, settle_ms: int = 2500) -> str:
    """One product page's rendered HTML, through one cloud browser.

    `domcontentloaded` plus a fixed settle, rather than `networkidle`: these
    marketplace pages hold long-poll and analytics connections open, so
    networkidle waits for the timeout on a page that was readable in two
    seconds. The settle is for the price and rating blocks, which arrive with
    the page state rather than in the initial HTML.

    Measured on live pages 2026-07-30: 9.7s, 12.8s and 23.3s. That is slow next
    to the Oxylabs fetch this can replace, and it is the whole reason the caller
    runs a lot of them at once.
    """
    async with _limiter():
        async with remote_browser() as rb:
            await _block_heavy_assets(rb.page)
            await rb.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await rb.page.wait_for_timeout(settle_ms)
            return await rb.page.content()


async def fetch_pages(urls: list[str], concurrency: int) -> list[tuple[str, str | None, str | None]]:
    """Fetch many product pages at once. Returns (url, html, error) per url, in
    the order given, and never raises — one dead page is one row that keeps its
    Lens data, exactly like the Oxylabs path it parallels.

    One session per page, not one session with many tabs. Tabs would be cheaper
    (sessions are billed per second and each create costs seconds), but every
    tab in a session shares its exit IP, and these are the sites this codebase
    already has a bot-detection history with. A session per page means a proxy
    IP per page, which is the difference the blocking risk turns on.

    `concurrency` must stay under the Browserbase plan's cap — 3 on Free, 25 on
    Developer. Over it, the session create fails outright rather than queueing,
    so this is a real ceiling and not a tuning hint.
    """
    if not urls:
        return []

    async def one(url: str):
        try:
            return url, await fetch_page(url), None
        except Exception as e:  # noqa: BLE001 — a page is never worth the request
            return url, None, f"{type(e).__name__}: {e}"

    results = await gather_limited([one(u) for u in urls], concurrency)
    out: list[tuple[str, str | None, str | None]] = []
    for url, result in zip(urls, results):
        # gather_limited returns exceptions rather than raising them, and an
        # exception escaping `one` means the failure was in the gather itself.
        if isinstance(result, BaseException):
            out.append((url, None, f"{type(result).__name__}: {result}"))
        else:
            out.append(result)
    return out


async def gather_limited(coros, limit: int):
    """asyncio.gather with a concurrency cap — Browserbase plans cap concurrent
    browsers (3 on Free), and exceeding it fails the session create outright."""
    sem = asyncio.Semaphore(limit)

    async def _run(coro):
        async with sem:
            return await coro

    return await asyncio.gather(*(_run(c) for c in coros), return_exceptions=True)
