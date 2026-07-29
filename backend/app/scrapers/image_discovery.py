"""Stage 1 of image sourcing: photo in, supplier-site results URL out.

The split this module exists to enforce: a **browser** is only needed to get
past the upload widget. Once a site has accepted the photo it hands back an
ordinary, shareable results URL (`.../img-search/<id>.html`,
`...?imageId=...`), and from that point on Zyte does the fetching and the
existing per-site parsers do the extraction — cheaper, faster, parallel, and
already unit-tested against fixtures.

So each recipe below is small on purpose. It clicks the camera, attaches the
file, waits for the URL to change into something that looks like results, and
returns. It never parses products.

One site can't be handled that way: Alibaba renders image results in-page
without always producing a stable results URL, so its recipe falls back to
returning the rendered HTML directly (`inline_html`). That's the exception, not
the pattern.

Recipe provenance matters when one breaks, so it's recorded per site in
`VERIFIED`: `made_in_china` and `alibaba` are ported from the local-Playwright
scrapers that were working against live sites; `aliexpress` uses the results-URL
shape documented in the README; `1688` is written from the site's current markup
but has NOT been confirmed end-to-end here — run spikes/tune_image_recipe.py
against it first and adjust selectors from the screenshot it dumps.
"""
import re
from dataclasses import dataclass, field

from .. import browserbase_client as bb

# Measured: a Made-in-China upload can accept the file and still take well over
# 30s to navigate to its results URL, and a session that misses the window
# usually succeeds on a fresh one. Two attempts at 30s left real searches
# failing that a third at 45s recovers.
MAX_UPLOAD_ATTEMPTS = 3
RESULTS_URL_TIMEOUT_MS = 45_000


@dataclass
class Recipe:
    site: str
    label: str
    entry_url: str
    file_input: str
    # URL shape that means "results have loaded". None => inline-HTML site.
    results_pattern: re.Pattern | None = None
    # Selectors clicked, in order, to reveal the file input. Missing ones are
    # skipped rather than failing the run — these sites A/B their homepages.
    open_upload: list[str] = field(default_factory=list)
    # Overlays/popups to dismiss before interacting.
    dismiss: list[str] = field(default_factory=list)
    # Selector that must exist before results HTML is worth reading (inline sites).
    inline_ready: str | None = None
    settle_ms: int = 6000
    # Content types this site's uploader accepts. None => no restriction found.
    accepts: tuple[str, ...] | None = None


RECIPES: dict[str, Recipe] = {
    "made_in_china": Recipe(
        site="made_in_china",
        label="Made-in-China",
        entry_url="https://www.made-in-china.com/",
        open_upload=["a.upload-img-camera"],
        dismiss=[".campaign-pop-mask"],
        # The generic input[type=file] also matches other widgets on this page.
        file_input="input.nail-search-camera-uploader, input[type=file]",
        results_pattern=re.compile(r"/img-search/[A-Za-z0-9]+\.html"),
        # Rejects webp/avif outright — see JPEG_ONLY_SITES below.
        accepts=("image/jpeg", "image/png", "image/bmp"),
    ),
    "aliexpress": Recipe(
        site="aliexpress",
        label="AliExpress",
        # NOT the homepage. The homepage intermittently throws a "checking if
        # you are a robot" modal inside a cross-origin iframe that top-frame
        # checks can't even see, and it blocks interaction. A search-results URL
        # carries the same header camera icon without the modal.
        entry_url="https://www.aliexpress.com/w/wholesale-product.html",
        open_upload=[
            ".esm--picture-search-btn--2xHyX4O",
            "[class*=picture-search-btn]",
            "[class*=search-camera]",
        ],
        file_input=".esm--upload-container--2PH_U0Y input[type=file], input[type=file]",
        results_pattern=re.compile(r"isNewImageSearch=y|imageId="),
    ),
    "1688": Recipe(
        site="1688",
        label="1688",
        entry_url="https://www.1688.com/",
        open_upload=[
            ".img-search-entry",
            "[class*=camera]",
            "[class*=imgSearch]",
        ],
        file_input="input[type=file]",
        results_pattern=re.compile(r"imageSearch|imageId=|/youyuan/"),
    ),
    "alibaba": Recipe(
        site="alibaba",
        label="Alibaba",
        entry_url="https://www.alibaba.com/",
        # The header "Image Search" text button. The .image-search-icon
        # tab-switcher looks like the right target and is NOT — it was confirmed
        # not to reveal a file input at all.
        open_upload=[
            "span.text-normal:text-is('Image Search')",
            "text=Image Search",
            "[data-search='switch-image-upload']",
        ],
        file_input="input.upload-file, input[type=file]",
        results_pattern=re.compile(r"SearchScene=imageTextSearch|imageId=|/picture/search-result"),
        inline_ready="[data-content='product-list'], .search-card, .organic-list",
    ),
}

# Selector/behaviour provenance, so a breakage can be triaged instead of guessed
# at. The three dated entries were confirmed against live sessions with a real
# product photo in the sibling Browserbase project (docs/SPIKES.md there); all
# three returned real, visually-correct supplier listings.
VERIFIED = {
    "made_in_china": "live-verified 2026-07-23 (jpg/png/bmp only; results at /img-search/{hash}.html)",
    "aliexpress": "live-verified 2026-07-23 (plain setInputFiles works; avoid homepage bot modal)",
    "alibaba": "live-verified 2026-07-23 (header Image Search text button, input.upload-file)",
    "1688": "UNVERIFIED — no spike exists for this site; tune before trusting",
}

# Made-in-China's uploader rejects anything outside this set, so a webp/avif
# query photo has to be transcoded first rather than silently failing upload.
JPEG_ONLY_SITES = {"made_in_china"}

CAPTCHA_MARKERS = ("Captcha Interception", "滑动验证", "Please slide to verify")


@dataclass
class Discovery:
    """What stage 1 hands to stage 2. Exactly one of results_url / inline_html
    is set on success; both are None on failure."""

    site: str
    results_url: str | None = None
    inline_html: str | None = None
    warnings: list[str] = field(default_factory=list)
    session_id: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.results_url or self.inline_html)


# Promo/campaign modals on Alibaba and Made-in-China have rotating class names,
# so matching by class was found to be unreliable. Geometry works instead: find
# whatever element is actually painted over the page centre-top and, if it's a
# full-viewport overlay rather than the page itself, remove it.
DISMISS_BY_GEOMETRY_JS = """
() => {
  let removed = 0;
  for (const [x, y] of [[window.innerWidth/2, 120], [window.innerWidth/2, window.innerHeight/2]]) {
    let el = document.elementFromPoint(x, y);
    while (el && el !== document.body) {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const covers = r.width >= window.innerWidth * 0.8 && r.height >= window.innerHeight * 0.5;
      if ((cs.position === 'fixed' || cs.position === 'absolute') && covers && +cs.zIndex > 100) {
        el.remove();
        removed++;
        break;
      }
      el = el.parentElement;
    }
  }
  return removed;
}
"""


async def _dismiss(page, selectors: list[str]) -> None:
    for sel in selectors:
        try:
            node = page.locator(sel)
            if await node.count() > 0:
                await node.first.evaluate("el => el.remove()")
        except Exception:
            continue
    try:
        await page.evaluate(DISMISS_BY_GEOMETRY_JS)
    except Exception:
        pass
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass


async def _open_upload_widget(page, selectors: list[str]) -> bool:
    """Click the first camera/upload trigger that exists. Returns False only if
    none of them do — some of these sites already expose a file input without
    any click, so callers treat False as "try the input anyway"."""
    for sel in selectors:
        try:
            node = page.locator(sel)
            if await node.count() == 0:
                continue
            await node.first.click(force=True, timeout=8000)
            await page.wait_for_timeout(1500)
            return True
        except Exception:
            continue
    return False


def _conform_image(recipe: Recipe, image_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    """Transcode the query photo if this site's uploader won't take its format.

    Made-in-China accepts only jpg/png/bmp; handing it a webp fails the upload
    silently, which would surface as "no results" rather than "wrong format".
    """
    if not recipe.accepts or content_type in recipe.accepts:
        return image_bytes, content_type
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as img:
            buffer = BytesIO()
            img.convert("RGB").save(buffer, format="JPEG", quality=88)
            return buffer.getvalue(), "image/jpeg"
    except Exception:
        # Better to attempt the upload than to fail before trying.
        return image_bytes, content_type


async def _attempt(recipe: Recipe, image_bytes: bytes, content_type: str) -> Discovery:
    image_bytes, content_type = _conform_image(recipe, image_bytes, content_type)
    ext = {"image/png": "png", "image/webp": "webp", "image/bmp": "bmp"}.get(content_type, "jpg")
    filename = f"query-{recipe.site}.{ext}"
    d = Discovery(site=recipe.site)

    async with bb.remote_browser() as rb:
        d.session_id = rb.session_id
        page = rb.page
        try:
            await page.goto(recipe.entry_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception:
            d.warnings.append(f"[{recipe.label}] could not load {recipe.entry_url}")
            return d

        title = (await page.title()) or ""
        if any(m in title for m in CAPTCHA_MARKERS):
            d.warnings.append(f"[{recipe.label}] challenged before upload (captcha)")
            return d

        await _dismiss(page, recipe.dismiss)
        await _open_upload_widget(page, recipe.open_upload)

        try:
            remote_path = await bb.upload_to_session(rb.session_id, image_bytes, filename)
            await bb.attach_file(rb, recipe.file_input, remote_path)
        except Exception as e:
            d.warnings.append(f"[{recipe.label}] file attach failed: {e}")
            return d

        if recipe.results_pattern is not None:
            url = await bb.wait_for_url(
                page,
                lambda u: bool(recipe.results_pattern.search(u or "")),
                timeout_ms=RESULTS_URL_TIMEOUT_MS,
            )
            if url:
                d.results_url = url.replace("http://", "https://", 1)
                return d

        # No results URL appeared. For inline sites that's expected; for the
        # others it's the failure path, but the rendered HTML is still worth
        # returning if the page clearly holds results — better a parse attempt
        # than a discarded upload.
        if recipe.inline_ready:
            await page.wait_for_timeout(recipe.settle_ms)
            title = (await page.title()) or ""
            if any(m in title for m in CAPTCHA_MARKERS):
                d.warnings.append(f"[{recipe.label}] challenged after upload (captcha)")
                return d
            try:
                await page.wait_for_selector(recipe.inline_ready, timeout=10_000)
            except Exception:
                d.warnings.append(f"[{recipe.label}] results never rendered after upload")
                return d
            d.inline_html = await page.content()
            return d

        d.warnings.append(f"[{recipe.label}] upload accepted but no results URL appeared")
        return d


async def discover(site: str, image_bytes: bytes, content_type: str) -> Discovery:
    """Run one site's upload recipe, retrying the whole flow on failure.

    Retries take a fresh cloud session deliberately: these failures are almost
    always IP/fingerprint challenges, and reusing the challenged session just
    reproduces them.
    """
    recipe = RECIPES.get(site)
    if recipe is None:
        return Discovery(site=site, warnings=[f"No image-search recipe for site {site!r}"])

    warnings: list[str] = []
    for attempt in range(1, MAX_UPLOAD_ATTEMPTS + 1):
        try:
            d = await _attempt(recipe, image_bytes, content_type)
        except bb.BrowserbaseError as e:
            return Discovery(site=site, warnings=[f"[{recipe.label}] {e}"])
        except Exception as e:
            d = Discovery(site=site, warnings=[f"[{recipe.label}] upload error: {e}"])

        warnings.extend(d.warnings)
        if d.ok:
            d.warnings = warnings
            return d
        if attempt < MAX_UPLOAD_ATTEMPTS:
            warnings.append(f"[{recipe.label}] retrying upload ({attempt}/{MAX_UPLOAD_ATTEMPTS})")

    warnings.append(
        f"[{recipe.label}] image search could not be completed after "
        f"{MAX_UPLOAD_ATTEMPTS} attempts — best-effort, try again shortly."
    )
    return Discovery(site=site, warnings=warnings)
