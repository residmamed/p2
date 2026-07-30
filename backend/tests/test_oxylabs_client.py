"""Oxylabs Web Scraper API client — no network.

This module could not be exercised against the live API while writing it (no
OXYLABS_USERNAME / OXYLABS_PASSWORD on this machine), so the request it builds
and the failures it distinguishes are pinned here against a mock transport
instead. The source names below are the ones the Oxylabs docs listed on
2026-07-29; they do move, and this file is where a rename would surface.
"""
import json

import httpx
import pytest

from app.credentials import KeyPool
from app.oxylabs_client import (
    OXYLABS_REALTIME_URL,
    SOURCE_FOR_SITE,
    OxylabsAuthError,
    OxylabsClient,
    OxylabsError,
    scrape_many,
    site_for_url,
)

PAGE = "<html><body>a product page</body></html>"


def transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def ok(content=PAGE, status=200, page_status=200):
    def handler(request):
        return httpx.Response(
            status, json={"results": [{"content": content, "status_code": page_status}]}
        )

    return handler


@pytest.mark.asyncio
async def test_alibaba_uses_its_dedicated_source_and_1688_falls_back_to_universal():
    """Alibaba has a dedicated target; 1688 and Taobao do not, so they go
    through `universal`, which is what the docs prescribe for sites without
    one. Sending 1688 to a source that doesn't exist fails the whole batch."""
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": [{"content": PAGE, "status_code": 200}]})

    oxy = OxylabsClient(username="u", password="p")
    async with transport(handler) as client:
        await oxy.scrape("https://www.alibaba.com/product-detail/x_1.html", "alibaba", client)
        await oxy.scrape("https://detail.1688.com/offer/1.html", "1688", client)

    payloads = [json.loads(r.content) for r in seen]
    assert [p["source"] for p in payloads] == ["alibaba", "universal"]
    assert payloads[0]["url"].endswith("x_1.html")
    assert all(str(r.url) == OXYLABS_REALTIME_URL for r in seen)
    # Rendering is off by default: it roughly triples cost and latency, and
    # every field this app reads is server-rendered.
    assert "render" not in payloads[0]


@pytest.mark.asyncio
async def test_alibaba_pins_its_exit_so_prices_arrive_in_one_currency():
    """Alibaba localises price to the exit IP and Oxylabs rotates exits
    worldwide. Measured: six unpinned fetches of one URL returned rand, lira
    and dollars; six pinned ones returned dollars every time. A table that
    quotes one supplier in rand and the next in dollars can't be compared."""
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"results": [{"content": PAGE, "status_code": 200}]})

    oxy = OxylabsClient(username="u", password="p")
    async with transport(handler) as client:
        await oxy.scrape("https://www.alibaba.com/product-detail/x.html", "alibaba", client)
        await oxy.scrape("https://detail.1688.com/offer/1.html", "1688", client)

    assert seen[0]["geo_location"] == "United States"
    # 1688 is a domestic Chinese site quoting CNY natively — a US exit would be
    # the wrong hint and risks the reachability that currently works.
    assert "geo_location" not in seen[1]


def test_source_map_covers_every_marketplace_the_pipeline_can_classify():
    for url, site in (
        ("https://www.alibaba.com/product-detail/x.html", "alibaba"),
        ("https://detail.1688.com/offer/1.html", "1688"),
        ("https://item.taobao.com/item.htm?id=1", "taobao"),
    ):
        assert site_for_url(url) == site
        assert site in SOURCE_FOR_SITE


@pytest.mark.asyncio
async def test_rejected_credentials_raise_the_auth_error_not_a_generic_one():
    """These are separate types because they need separate responses: an auth
    failure will hit every URL in the batch and belongs in the operator-facing
    `errors`, while a generic failure is one degraded row."""
    def handler(request):
        return httpx.Response(401, json={"message": "Invalid credentials"})

    oxy = OxylabsClient(username="u", password="wrong")
    async with transport(handler) as client:
        with pytest.raises(OxylabsAuthError, match="OXYLABS_USERNAME"):
            await oxy.scrape("https://www.alibaba.com/x.html", "alibaba", client)


@pytest.mark.asyncio
async def test_unconfigured_client_raises_before_any_request(monkeypatch):
    """Patched at the credential-pool level rather than passing empty strings:
    an explicit "" falls back to the configured account, the same way
    ZyteClient's key does, so this must simulate an unset env to mean anything.

    The pool is what "unset" means now — Oxylabs can hold several accounts
    (app/credentials.py), and an empty pool is the only way to say there is no
    account at all."""
    from app import oxylabs_client

    monkeypatch.setattr(oxylabs_client.credentials, "OXYLABS", KeyPool("OXYLABS", []))
    oxy = OxylabsClient()
    assert oxy.is_configured() is False
    with pytest.raises(OxylabsAuthError, match="not configured"):
        await oxy.scrape("https://www.alibaba.com/x.html", "alibaba")


@pytest.mark.asyncio
async def test_a_404_from_the_target_is_a_failure_not_a_page_to_parse():
    """Oxylabs answers 200 for having done the job even when the target
    answered 404. Parsing that page would invent a supplier out of an error
    page's chrome."""
    oxy = OxylabsClient(username="u", password="p")
    async with transport(ok(content="<html>Not found</html>", page_status=404)) as client:
        with pytest.raises(OxylabsError, match="404"):
            await oxy.scrape("https://www.alibaba.com/x.html", "alibaba", client)


@pytest.mark.asyncio
async def test_an_empty_page_raises_rather_than_returning_an_empty_string():
    oxy = OxylabsClient(username="u", password="p")
    async with transport(ok(content="   ")) as client:
        with pytest.raises(OxylabsError, match="empty page"):
            await oxy.scrape("https://www.alibaba.com/x.html", "alibaba", client)


@pytest.mark.asyncio
async def test_parsed_json_where_html_was_expected_is_reported_as_a_changed_contract():
    """If a source starts honouring `parse: true`, handing a dict to an HTML
    parser would look like an unparseable page rather than a moved API."""
    oxy = OxylabsClient(username="u", password="p")
    async with transport(ok(content={"title": "x"})) as client:
        with pytest.raises(OxylabsError, match="contract has changed"):
            await oxy.scrape("https://www.alibaba.com/x.html", "alibaba", client)


@pytest.mark.asyncio
async def test_a_gateway_error_is_retried_once_and_can_succeed():
    """504s came back from Oxylabs' own gateway in milliseconds during live
    testing and turned 3-of-4 into 4-of-4 on retry."""
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(504, text="gateway timeout")
        return httpx.Response(200, json={"results": [{"content": PAGE, "status_code": 200}]})

    oxy = OxylabsClient(username="u", password="p")
    async with transport(handler) as client:
        assert await oxy.scrape("https://www.alibaba.com/x.html", "alibaba", client) == PAGE
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_retries_are_bounded_and_a_persistent_error_surfaces():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, text="unavailable")

    oxy = OxylabsClient(username="u", password="p")
    async with transport(handler) as client:
        with pytest.raises(OxylabsError, match="503"):
            await oxy.scrape("https://www.alibaba.com/x.html", "alibaba", client)
    assert len(calls) == 2, "one retry, not a loop"


@pytest.mark.asyncio
async def test_a_timeout_is_not_retried():
    """A timeout has already spent the full per-URL budget; spending it twice
    doubles the whole request's latency instead of degrading one row."""
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("too slow", request=request)

    oxy = OxylabsClient(username="u", password="p")
    async with transport(handler) as client:
        with pytest.raises(OxylabsError, match="timed out"):
            await oxy.scrape("https://www.alibaba.com/x.html", "alibaba", client)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_missing_results_array_is_an_error():
    def handler(request):
        return httpx.Response(200, json={"results": []})

    oxy = OxylabsClient(username="u", password="p")
    async with transport(handler) as client:
        with pytest.raises(OxylabsError, match="no results"):
            await oxy.scrape("https://www.alibaba.com/x.html", "alibaba", client)


@pytest.mark.asyncio
async def test_scrape_many_returns_one_entry_per_url_in_order_and_never_raises():
    """A batch must degrade per URL. If one bad page could raise out of here,
    a single dead listing would cost every other supplier in the search."""
    urls = [
        "https://www.alibaba.com/product-detail/good.html",
        "https://www.alibaba.com/product-detail/bad.html",
        "https://detail.1688.com/offer/2.html",
    ]

    class Flaky(OxylabsClient):
        async def scrape(self, url, site=None, client=None):
            if "bad" in url:
                raise OxylabsError("boom")
            return PAGE

    outcomes = await scrape_many(urls, Flaky(username="u", password="p"))
    assert [u for u, _, _ in outcomes] == urls
    assert [h is not None for _, h, _ in outcomes] == [True, False, True]
    assert isinstance(outcomes[1][2], OxylabsError)


@pytest.mark.asyncio
async def test_an_unexpected_exception_is_caught_per_url():
    class Exploding(OxylabsClient):
        async def scrape(self, url, site=None, client=None):
            raise RuntimeError("something nobody predicted")

    outcomes = await scrape_many(["https://www.alibaba.com/x.html"], Exploding(username="u", password="p"))
    assert outcomes[0][1] is None
    assert isinstance(outcomes[0][2], OxylabsError)
