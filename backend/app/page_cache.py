"""A short-lived disk cache for enriched product pages, keyed by product URL.

`lens_cache` caches step 1 for 30 days and says, deliberately, that step 2 is
left uncached: "A month-old list of candidate URLs re-priced live is a good
trade; a month-old price is not." That reasoning is right and this does not
overturn it — it narrows it.

What changed is the cost of step 2. When it was an Oxylabs fetch it was
sub-second and not worth caching. It is now a cloud browser per page at roughly
two seconds each under concurrency, and the same demo run repeated pays it
again in full.

So the TTL here is hours, not days, and it is configurable. A price that moved
in the last few hours is a price that moved between two page loads of a search
the user is still looking at; a price that moved in the last month is ordinary.
Set `SUPPLIER_PAGE_CACHE_TTL_MINUTES=0` to switch this off entirely, which is
the right setting if quotes are being acted on rather than demonstrated.

Stores the PARSED fields rather than the HTML. The HTML is 300-800KB a page and
25 of them per search, which is a quarter-gigabyte of disk for data whose only
use is to be re-parsed into a few hundred bytes. The cost is that a parser
improvement does not apply to already-cached pages until they expire — which,
at a TTL measured in hours, resolves itself.
"""
import hashlib
import json
import time
from dataclasses import asdict, fields
from pathlib import Path

from .config import settings
from .parsing.marketplace_product import ParsedProduct

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def ttl_seconds() -> int:
    return max(0, settings.supplier_page_cache_ttl_minutes) * 60


def enabled() -> bool:
    return ttl_seconds() > 0


def cache_dir() -> Path:
    configured = Path(settings.supplier_page_cache_dir)
    return configured if configured.is_absolute() else _BACKEND_DIR / configured


def _path_for(url: str) -> Path:
    return cache_dir() / (hashlib.sha256(url.strip().encode("utf-8")).hexdigest() + ".json")


def get(url: str) -> ParsedProduct | None:
    """The cached parse for this URL, or None if absent, expired or unreadable.

    A corrupt or half-written file is a miss and is removed, never an exception:
    a cache is an optimisation and must not be able to fail a search.
    """
    if not enabled():
        return None
    path = _path_for(url)
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or time.time() - raw.get("stored_at", 0) > ttl_seconds():
        path.unlink(missing_ok=True)
        return None
    payload = raw.get("parsed")
    if not isinstance(payload, dict):
        path.unlink(missing_ok=True)
        return None
    # Only fields the dataclass still declares. A cache written before a field
    # was renamed must not blow up the constructor on the way back in.
    known = {f.name for f in fields(ParsedProduct)}
    try:
        return ParsedProduct(**{k: v for k, v in payload.items() if k in known})
    except TypeError:
        path.unlink(missing_ok=True)
        return None


def put(url: str, parsed: ParsedProduct) -> None:
    """Store one page's parsed fields. Never raises — see `get`."""
    if not enabled():
        return
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        path = _path_for(url)
        # Written to a temp name and moved, so a reader never sees half a file.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"stored_at": time.time(), "parsed": asdict(parsed)}))
        tmp.replace(path)
    except (OSError, TypeError, ValueError):
        return
