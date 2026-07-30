"""A 30-day disk cache for Google Lens results, keyed by the image.

Every Lens search costs SerpApi quota and about a second of the user's wait, and
the answer for a given photo does not meaningfully change between Tuesdays.
Which supplier listings *exist* for a product is slow-moving; their prices are
not, but those come from the enrichment step, which is deliberately left
uncached. So this caches step 1 only. A month-old list of candidate URLs
re-priced live is a good trade; a month-old price is not.

**Keying.** The brief asks for SHA256 of the image bytes, and that is what an
upload gets: the same photo re-uploaded under a different filename hits the same
entry. A caller who passes `image_url` instead is keyed on the URL, because
fetching the bytes purely to hash them would add a round trip to *every*
request, including the ones the cache is supposed to make fast — and the URL is
what SerpApi consumes anyway, so it is the honest identity for that call. The
cost is that the same picture reached both ways occupies two entries.

**Storage.** One JSON file per key under `backend/.cache/lens/`, because this
app still has no database and a dict in a module would be lost on every reload.
Expiry is checked on read and the stale file deleted, so a cache that is never
read never grows a reaper thread it doesn't need.
"""
import hashlib
import json
import os
import time
from pathlib import Path

from .config import settings

TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days, per the brief

# Cache directory paths resolve against backend/ — the directory uvicorn runs
# from — so a relative setting behaves the same however the app is launched.
_BACKEND_DIR = Path(__file__).resolve().parent.parent


def key_for_bytes(image_bytes: bytes) -> str:
    return "sha256:" + hashlib.sha256(image_bytes).hexdigest()


def key_for_url(image_url: str) -> str:
    return "url:" + hashlib.sha256(image_url.strip().encode("utf-8")).hexdigest()


def cache_dir() -> Path:
    configured = Path(settings.lens_cache_dir)
    return configured if configured.is_absolute() else _BACKEND_DIR / configured


def _path_for(key: str) -> Path:
    # The key already carries its own prefix; ':' is legal in a POSIX filename
    # but noisy, and on Windows it is not legal at all.
    return cache_dir() / (key.replace(":", "_") + ".json")


def get(key: str) -> dict | None:
    """The cached payload, or None if absent, expired or unreadable.

    A corrupt file is treated as a miss and removed rather than raised: the
    whole point of a cache is that losing it costs a re-fetch, never a request.
    """
    path = _path_for(key)
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        return None

    try:
        entry = json.loads(raw)
        stored_at = float(entry["stored_at"])
        payload = entry["payload"]
    except (ValueError, KeyError, TypeError):
        _discard(path)
        return None

    if time.time() - stored_at > TTL_SECONDS:
        _discard(path)
        return None
    return payload if isinstance(payload, dict) else None


def put(key: str, payload: dict) -> None:
    """Store a payload. Failing to write is not an error the caller cares about
    — the result is already in hand; only the next search pays."""
    path = _path_for(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash mid-write leaves the old entry or no
        # entry, never a half-file that reads as corrupt on the next request.
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps({"stored_at": time.time(), "payload": payload}),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        return


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def age_seconds(key: str) -> float | None:
    """How old a live entry is, for reporting a cache hit honestly in the
    response rather than passing month-old data off as fresh."""
    try:
        entry = json.loads(_path_for(key).read_text(encoding="utf-8"))
        return max(0.0, time.time() - float(entry["stored_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
