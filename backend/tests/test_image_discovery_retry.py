"""Retry policy for the browser upload path — no network.

The distinction pinned here is the one that dominated a live sourcing run's
wall clock: a failure a fresh cloud session can fix, versus one it cannot.
Retrying the second kind cost three full browser sessions per site per product
to reach an identical dead end.
"""
import pytest

from app.scrapers import image_discovery
from app.scrapers.image_discovery import Discovery


@pytest.mark.asyncio
async def test_a_missing_file_input_is_not_retried(monkeypatch):
    """"No file input matched" means the recipe's selector isn't on the page.
    A new IP and fingerprint land on the same page and fail the same way."""
    attempts = []

    async def fake_attempt(recipe, image_bytes, content_type):
        attempts.append(1)
        d = Discovery(site=recipe.site)
        d.warnings.append(
            f"[{recipe.label}] file attach failed: No file input matched 'input[type=file]'"
        )
        d.structural = True
        return d

    monkeypatch.setattr(image_discovery, "_attempt", fake_attempt)
    result = await image_discovery.discover("1688", b"x", "image/jpeg")

    assert len(attempts) == 1, "a structural failure must not spend more sessions"
    assert not result.ok
    assert any("recipe needs updating" in w for w in result.warnings)
    # The misleading "tried 3 times" line must not appear when we tried once.
    assert not any("after 3 attempts" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_a_transient_failure_still_gets_its_retries(monkeypatch):
    """A challenge or a slow navigation is exactly what a fresh session fixes —
    that behaviour is the reason the retry loop exists and must survive."""
    attempts = []

    async def fake_attempt(recipe, image_bytes, content_type):
        attempts.append(1)
        d = Discovery(site=recipe.site)
        d.warnings.append(f"[{recipe.label}] results never rendered after upload")
        return d

    monkeypatch.setattr(image_discovery, "_attempt", fake_attempt)
    result = await image_discovery.discover("1688", b"x", "image/jpeg")

    assert len(attempts) == image_discovery.MAX_UPLOAD_ATTEMPTS
    assert not result.ok


@pytest.mark.asyncio
async def test_a_retry_that_succeeds_returns_the_result(monkeypatch):
    attempts = []

    async def fake_attempt(recipe, image_bytes, content_type):
        attempts.append(1)
        d = Discovery(site=recipe.site)
        if len(attempts) == 1:
            d.warnings.append("transient")
            return d
        d.results_url = "https://example.com/results"
        return d

    monkeypatch.setattr(image_discovery, "_attempt", fake_attempt)
    result = await image_discovery.discover("1688", b"x", "image/jpeg")

    assert result.ok and result.results_url == "https://example.com/results"
    assert len(attempts) == 2
